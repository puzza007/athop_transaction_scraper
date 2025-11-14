# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AT HOP (Auckland Transport) transaction scraper that automatically fetches public transport card transactions and stores them in a SQLite database with optional Slack notifications.

## Key Commands

### Development
```bash
# Install dependencies (requires uv)
uv sync

# Set up environment variables (copy and edit)
cp .env.example .env
# Edit .env with your credentials

# Run the scraper locally
uv run python athop_transaction_scraper.py

# Run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### Building
```bash
# Build Docker image
docker-compose build

# Build multi-platform image (for CI/CD)
docker buildx build --platform linux/amd64,linux/arm64 -t puzza007/athop_transaction_scraper:latest .
```

## Architecture Overview

### Core Components

1. **Main Scraper** (`athop_transaction_scraper.py`): Single-file Python application that:
   - Authenticates with AT HOP website using Selenium for Azure AD B2C authentication
   - Fetches transaction data from JSON API endpoints
   - Stores new transactions in SQLite database
   - Sends Slack notifications with Block Kit formatting for new transactions
   - Runs in continuous loop with configurable interval
   - Uses Pacific/Auckland timezone for logging and timestamps

2. **Database**: SQLite database at `/data/athop.db` with single `transactions` table
   - Composite primary key: (card_id, cardtransactionid)
   - Auto-created on first run using `schema.sql`

3. **Docker Setup**: Lightweight build that:
   - Uses uv for fast dependency management
   - Installs Chrome (AMD64) or Chromium (ARM64) for Selenium
   - Multi-architecture support (linux/amd64, linux/arm64)

### Authentication Flow

The scraper uses Selenium WebDriver for browser automation:
1. Launch headless Chrome/Chromium browser
2. Navigate to AT HOP login page
3. Handle Azure AD B2C authentication flow
4. Transfer session cookies to requests library for API access

### Environment Variables

Configuration is managed via a `.env` file (see `.env.example` for template):

**Required:**
- `AT_USERNAME`: AT HOP account email
- `AT_PASSWORD`: Account password
- `AT_CARDS`: Comma-separated HOP card numbers, optionally with names in format `card_number:card_name` (e.g., `123456:Paul,789012:Family`)
- `AT_DATABASE_FILE`: SQLite database path

**Optional:**
- `AT_PERIOD`: Scraping interval in seconds (default: 3600)
- `AT_STARTUP_DELAY`: Initial delay before first scrape in seconds (default: 60)
- `AT_SLACK_API_TOKEN`: Slack bot token for notifications
- `AT_SLACK_CHANNEL`: Slack channel ID for notifications (e.g., `#notifications`)

The `.env` file is git-ignored to prevent credential leaks. Use `.env.example` as a template.

### Important Notes

- Litestream can be used for automated SQLite backups to SFTP
- No test suite exists - manual testing required
- CI/CD automatically builds and pushes multi-platform Docker images on master branch commits