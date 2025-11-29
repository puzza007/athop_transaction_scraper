# AT HOP Transaction Scraper

Automatically scrapes Auckland Transport HOP card transactions and stores them in SQLite with optional Slack notifications.

## Quick Start

```bash
# Copy and edit environment variables
cp .env.example .env
# Edit .env with your credentials

# Run with Docker
docker-compose up -d

# View logs
docker-compose logs -f
```

Your data will be in `./data/athop.db`

## Configuration

All configuration via `.env` file:

**Required:**
- `AT_USERNAME` - AT HOP account email
- `AT_PASSWORD` - Account password
- `AT_CARDS` - Card numbers (comma-separated, optional names: `123:Name,456:Other`)

**Optional:**
- `AT_PERIOD` - Scrape interval in seconds (default: 3600)
- `AT_STARTUP_DELAY` - Initial delay in seconds (default: 60)
- `AT_SLACK_API_TOKEN` - Slack bot token for notifications
- `AT_SLACK_CHANNEL` - Slack channel (e.g., `#notifications`)

See `.env.example` for template.

## Development

```bash
# Install dependencies
uv sync

# Run locally
uv run python athop_transaction_scraper.py

# Format and type check
uv run black .
uv run mypy athop_transaction_scraper.py
```
