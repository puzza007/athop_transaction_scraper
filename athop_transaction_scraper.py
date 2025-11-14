#!/usr/bin/env python

import logging
import os
import shutil
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, List, NamedTuple, Optional
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from slack import WebClient  # type: ignore[import-not-found]
from slack.errors import SlackApiError  # type: ignore[import-not-found]

# Set timezone to Pacific/Auckland
AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
os.environ["TZ"] = "Pacific/Auckland"

# Configure logging with Auckland timezone
logging.Formatter.converter = lambda *args: datetime.now(AUCKLAND_TZ).timetuple()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("athop")


class Transaction(NamedTuple):
    """Structured transaction data."""

    card_id: str
    card_name: Optional[str]
    cardtransactionid: str
    description: str
    location: str
    transactiondatetime: str
    hop_balance_display: str
    value: Optional[float]
    value_display: str
    journey_id: str
    refundrequested: int
    refundable_value: float
    transaction_type_description: str
    transaction_type: str


class Config:
    """Configuration management with validation."""

    def __init__(self) -> None:
        self.username = self._get_required("AT_USERNAME")
        self.password = self._get_required("AT_PASSWORD")
        self.cards = self._parse_cards(self._get_required("AT_CARDS"))
        self.database_file = self._get_required("AT_DATABASE_FILE")
        self.period = self._get_int_env("AT_PERIOD", 3600)
        self.startup_delay = self._get_int_env("AT_STARTUP_DELAY", 60)
        self.slack_token = os.getenv("AT_SLACK_API_TOKEN")
        self.slack_channel = os.getenv("AT_SLACK_CHANNEL")
        self.max_retries = self._get_int_env("AT_MAX_RETRIES", 3)
        self.request_timeout = self._get_int_env("AT_REQUEST_TIMEOUT", 30)

    @staticmethod
    def _get_required(key: str) -> str:
        value = os.getenv(key)
        if not value:
            logger.error(f"Required environment variable {key} is not set")
            sys.exit(1)
        return value

    @staticmethod
    def _get_int_env(key: str, default: int) -> int:
        """Get integer environment variable with validation."""
        value_str = os.getenv(key, str(default))
        try:
            return int(value_str)
        except ValueError:
            logger.error(f"Invalid {key} value: {value_str!r} (must be an integer)")
            sys.exit(1)

    @staticmethod
    def _parse_cards(cards_str: str) -> Dict[str, Optional[str]]:
        """Parse cards string into dict of card_id: card_name.

        Supports format "card_id" or "card_id:card_name"
        Example: "7824670200018019639:Paul,7824670200008525496:Family"
        """
        cards: Dict[str, Optional[str]] = {}
        for card_entry in cards_str.split(","):
            card_entry = card_entry.strip()
            if ":" in card_entry:
                card_id, card_name = card_entry.split(":", 1)
                cards[card_id.strip()] = card_name.strip()
            else:
                cards[card_entry] = None
        return cards


class ATHopScraper:
    """Main scraper class with improved error handling and structure."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.session: Optional[requests.Session] = None
        self.slack_client: Optional[WebClient] = None
        self._init_slack()

    def _init_slack(self) -> None:
        """Initialize Slack client if credentials provided."""
        if self.config.slack_token and self.config.slack_channel:
            self.slack_client = WebClient(token=self.config.slack_token)
            logger.info(
                f"Slack client initialized for channel {self.config.slack_channel}"
            )
        else:
            logger.info("Slack notifications disabled (missing token or channel)")

    def _create_session(self) -> requests.Session:
        """Create requests session with retry strategy."""
        session = requests.Session()
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        # Note: timeout should be set per-request, not on session
        return session

    def _create_chrome_options(self) -> Options:
        """Create Chrome options for headless mode."""
        options = Options()
        for arg in [
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
        ]:
            options.add_argument(arg)

        # Set binary location for Chromium (used on ARM64/Linux)
        chromium_path = shutil.which("chromium") or shutil.which("chromium-browser")
        if chromium_path:
            options.binary_location = chromium_path

        return options

    def login(self) -> bool:
        """Perform login using Selenium to handle Azure AD B2C authentication."""
        chrome_options = self._create_chrome_options()
        driver = None

        try:
            logger.info("Starting headless browser for authentication...")

            # Create Chrome driver with optional explicit chromedriver path
            chromedriver_path = shutil.which("chromedriver")
            if chromedriver_path:
                service = Service(executable_path=chromedriver_path)
                driver = webdriver.Chrome(service=service, options=chrome_options)  # type: ignore[call-arg]
            else:
                driver = webdriver.Chrome(options=chrome_options)

            # Navigate to login page
            logger.info("Navigating to login page...")
            driver.get("https://at.govt.nz/account/SignIn/MyATAuth")

            # Wait for the Azure AD B2C login form to load
            wait = WebDriverWait(driver, 20)

            # Wait for email field to be clickable
            logger.info("Waiting for login form...")
            email_field = wait.until(EC.element_to_be_clickable((By.ID, "signInName")))

            # Enter credentials
            logger.info("Entering credentials...")
            email_field.send_keys(self.config.username)

            # Wait for password field to be clickable
            password_field = wait.until(EC.element_to_be_clickable((By.ID, "password")))
            password_field.send_keys(self.config.password)

            # Wait for sign in button to be clickable
            sign_in_button = wait.until(EC.element_to_be_clickable((By.ID, "next")))
            sign_in_button.click()

            # Wait for authentication to complete - the page might show a "Continue" button or auto-submit
            logger.info("Waiting for authentication to complete...")
            time.sleep(3)  # Give time for the form to process

            # Check if we're still on the federation page (might need to click Continue)
            if "federation.aucklandtransport.govt.nz" in driver.current_url:
                logger.info("Still on federation page, looking for continue button...")
                try:
                    # Look for any submit button that continues the flow
                    continue_button = wait.until(
                        EC.element_to_be_clickable(
                            (
                                By.CSS_SELECTOR,
                                "button[type='submit'], input[type='submit']",
                            )
                        )
                    )
                    continue_button.click()
                    time.sleep(2)
                except (TimeoutException, NoSuchElementException) as e:
                    logger.info(
                        f"No continue button found: {e}, page might auto-submit"
                    )

            # Now wait for the redirect to at.govt.nz (with longer timeout as it involves OIDC flow)
            wait_long = WebDriverWait(driver, 30)
            wait_long.until(
                EC.url_contains("at.govt.nz"),
                message="Timeout waiting for redirect to at.govt.nz after login",
            )

            logger.info(
                f"Successfully authenticated! Current URL: {driver.current_url}"
            )

            # Transfer cookies from Selenium to requests session
            self.session = self._create_session()
            if self.session is None:
                raise RuntimeError("Failed to create session")

            selenium_cookies = driver.get_cookies()
            for cookie in selenium_cookies:
                self.session.cookies.set(
                    cookie["name"], cookie["value"], domain=cookie.get("domain")
                )

            logger.info(
                f"Transferred {len(selenium_cookies)} cookies to requests session"
            )
            logger.info("Login successful")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout during login: {e}")
            if driver:
                try:
                    screenshot_path = "/tmp/login_error.png"
                    driver.save_screenshot(screenshot_path)
                    logger.error(f"Screenshot saved to {screenshot_path}")
                except WebDriverException as e:
                    logger.debug(f"Failed to save screenshot: {e}")
            return False
        except WebDriverException as e:
            logger.error(f"Login failed: {e}")
            if driver:
                try:
                    driver.save_screenshot("/tmp/login_error.png")
                except WebDriverException as e:
                    logger.debug(f"Failed to save screenshot: {e}")
            return False
        finally:
            if driver:
                driver.quit()

    @contextmanager
    def database_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections."""
        conn = None
        try:
            conn = sqlite3.connect(self.config.database_file)
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def ensure_database(self) -> None:
        """Initialize database schema from file or embedded schema."""
        with self.database_connection() as conn:
            # Check if table exists
            res = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'"
            ).fetchone()

            if res is not None:
                return

            # Try to load schema from file first
            schema_file = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(schema_file):
                with open(schema_file, "r") as f:
                    schema = f.read()
            else:
                # Fallback to embedded schema
                schema = """
                CREATE TABLE transactions (
                    card_id TEXT,
                    card_name TEXT,
                    cardtransactionid TEXT,
                    description TEXT,
                    location TEXT,
                    transactiondatetime TEXT,
                    hop_balance_display TEXT,
                    value REAL,
                    value_display TEXT,
                    journey_id TEXT,
                    refundrequested INTEGER,
                    refundable_value REAL,
                    transaction_type_description TEXT,
                    transaction_type TEXT,
                    PRIMARY KEY (card_id, cardtransactionid)
                )
                """

            conn.execute(schema)
            logger.info("Database initialized")

    def fetch_transactions(self, card_id: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch transactions for a single card."""
        if not self.session:
            logger.error("No active session")
            return None

        try:
            response = self.session.get(
                f"https://at.govt.nz/hop/cards/{card_id}/transactions",
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()

            data = response.json()
            return data.get("Transactions", [])

        except requests.HTTPError as e:
            logger.error(f"HTTP error fetching card {card_id}: {e}")
        except requests.exceptions.JSONDecodeError:
            logger.error(f"Invalid JSON response for card {card_id}")
        except requests.RequestException as e:
            logger.error(f"Request error for card {card_id}: {e}")

        return None

    def process_transaction(
        self, card_id: str, card_name: Optional[str], transaction: Dict[str, Any]
    ) -> Optional[Transaction]:
        """Process a single transaction into Transaction object."""
        try:
            # Skip pending transactions
            if transaction.get("description") == "TRANSACTION(S) PENDING":
                logger.debug(f"Skipping pending transaction for card {card_id}")
                return None

            return Transaction(
                card_id=card_id,
                card_name=card_name,
                cardtransactionid=transaction["cardtransactionid"],
                description=transaction["description"],
                location=transaction["location"],
                transactiondatetime=transaction["transactiondatetime"],
                hop_balance_display=transaction["hop-balance-display"],
                value=transaction.get("value"),
                value_display=transaction["value-display"],
                journey_id=transaction["journey-id"],
                refundrequested=transaction["refundrequested"],
                refundable_value=transaction["refundable-value"],
                transaction_type_description=transaction[
                    "transaction-type-description"
                ],
                transaction_type=transaction["transaction-type"],
            )
        except KeyError as e:
            logger.error(f"Missing required field in transaction: {e}")
            return None

    def send_slack_notification(self, txn: Transaction) -> None:
        """Send Slack notification for new transaction."""
        if not self.slack_client:
            return

        # Determine emoji based on transaction type
        emoji_map = {"Bus": ":bus:", "Train": ":train:", "Ferry": ":ferry:"}
        emoji = next(
            (
                emoji
                for keyword, emoji in emoji_map.items()
                if keyword in txn.description
            ),
            ":credit_card:",
        )

        # Format the card name for header
        card_display = f"{txn.card_name}'s Card" if txn.card_name else "HOP Card"

        # Format amount - use value_display if available, otherwise format value
        amount_display = (
            txn.value_display
            if txn.value_display
            else (f"${txn.value:.2f}" if txn.value is not None else "N/A")
        )

        # Build rich block layout
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} New HOP Transaction",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Card:*\n{card_display}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Date/Time:*\n{txn.transactiondatetime}",
                    },
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Description:*\n{txn.description}"},
                    {"type": "mrkdwn", "text": f"*Location:*\n{txn.location}"},
                ],
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Amount:*\n{amount_display}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Balance:*\n{txn.hop_balance_display}",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Transaction ID: {txn.cardtransactionid} | Type: {txn.transaction_type_description}",
                    }
                ],
            },
            {"type": "divider"},
        ]

        try:
            self.slack_client.chat_postMessage(
                channel=self.config.slack_channel,
                icon_emoji=":robot_face:",
                blocks=blocks,
                text=f"New HOP transaction: {txn.description} at {txn.location}",  # Fallback text
            )
        except SlackApiError as e:
            logger.error(f"Slack notification failed: {e}")

    def scrape_card(self, card_id: str, card_name: Optional[str] = None) -> int:
        """Scrape transactions for a single card. Returns count of new transactions."""
        transactions = self.fetch_transactions(card_id)
        if transactions is None:
            return 0

        new_count = 0
        with self.database_connection() as conn:
            for transaction in transactions:
                txn = self.process_transaction(card_id, card_name, transaction)
                if not txn:
                    continue

                try:
                    conn.execute(
                        """INSERT INTO transactions (
                            card_id, card_name, cardtransactionid, description, location,
                            transactiondatetime, hop_balance_display, value,
                            value_display, journey_id, refundrequested,
                            refundable_value, transaction_type_description,
                            transaction_type
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        txn,
                    )
                    new_count += 1
                    logger.info(
                        f"New transaction: {txn.cardtransactionid} for card {card_id} ({card_name or 'unnamed'})"
                    )
                    self.send_slack_notification(txn)

                except sqlite3.IntegrityError:
                    # Transaction already exists
                    pass

        return new_count

    def run_once(self) -> bool:
        """Run one complete scraping cycle. Returns success status."""
        start_time = datetime.now(AUCKLAND_TZ)

        # Ensure we have a valid session
        if not self.session or not self._test_session():
            logger.info("Creating new session")
            if not self.login():
                return False

        # Scrape all cards
        total_new = 0
        for card_id, card_name in self.config.cards.items():
            logger.info(f"Scraping card: {card_id} ({card_name or 'unnamed'})")
            new_count = self.scrape_card(card_id, card_name)
            total_new += new_count

        duration = (datetime.now(AUCKLAND_TZ) - start_time).total_seconds()
        logger.info(
            f"Scraping completed in {duration:.1f}s. New transactions: {total_new}"
        )
        return True

    def _test_session(self) -> bool:
        """Test if current session is still valid."""
        if not self.session:
            return False

        try:
            # Try to access a protected endpoint with the first card
            first_card_id = next(iter(self.config.cards.keys()))
            r = self.session.get(
                f"https://at.govt.nz/hop/cards/{first_card_id}/transactions", timeout=10
            )
            return r.status_code == 200
        except Exception:
            return False

    def run(self) -> None:
        """Main run loop with error recovery."""
        logger.info("AT HOP scraper starting...")

        # Initial setup
        self.ensure_database()

        # Wait before first run (in case of restart due to error)
        if self.config.startup_delay > 0:
            logger.info(f"Waiting {self.config.startup_delay}s before first run...")
            time.sleep(self.config.startup_delay)

        consecutive_failures = 0
        while True:
            try:
                if self.run_once():
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                # Exponential backoff on failures (capped at 30 minutes)
                if consecutive_failures > 0:
                    wait_time = min(
                        60 * (2 ** (consecutive_failures - 1)), 1800  # Cap at 30min
                    )
                    logger.warning(
                        f"Backing off for {wait_time}s due to {consecutive_failures} failures"
                    )
                    time.sleep(wait_time)
                else:
                    logger.info(f"Sleeping for {self.config.period}s...")
                    time.sleep(self.config.period)

            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                break
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")
                consecutive_failures += 1
                time.sleep(60)


if __name__ == "__main__":
    config = Config()
    scraper = ATHopScraper(config)
    scraper.run()
