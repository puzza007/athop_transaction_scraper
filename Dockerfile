FROM python:3.13-slim-bookworm

# Set timezone to Pacific/Auckland
ENV TZ=Pacific/Auckland
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install Chromium and its matching driver from Debian (works on amd64 and
# arm64, and is much smaller than Google Chrome). fonts-liberation gives
# headless Chromium sane default fonts.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        chromium \
        chromium-driver && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency file
COPY pyproject.toml /app/
WORKDIR /app

# Install dependencies using uv (just the dependencies, not as a package)
RUN uv pip install --system -r pyproject.toml

# Copy application files
COPY athop_transaction_scraper.py gtfs.py schema.sql /app/

# Run as an unprivileged user. UID/GID should match the owner of the
# bind-mounted data directory on the host (see docker-compose.yml).
ARG UID=1000
ARG GID=1000
RUN (getent group "$GID" >/dev/null || groupadd -g "$GID" app) && \
    useradd -m -u "$UID" -g "$GID" -s /usr/sbin/nologin app && \
    mkdir -p /data && chown "$UID:$GID" /data
ENV HOME=/home/app
USER app

VOLUME /data

CMD ["python", "/app/athop_transaction_scraper.py"]
