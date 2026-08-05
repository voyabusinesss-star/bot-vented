# Discord interactions + webhook Whop + scrape Vinted (Railway)
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    WHOP_WEBHOOK_HOST=0.0.0.0 \
    WHOP_WEBHOOK_PORT=8080 \
    SCRAPE_HEADLESS=true \
    ENABLE_SCRAPE=1 \
    ENABLE_DETECTOR=0 \
    ENABLE_FICHES=0

# Dépendances système Playwright / Chromium (Docker / Railway)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      fonts-liberation \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libcups2 \
      libdbus-1-3 \
      libdrm2 \
      libgbm1 \
      libgtk-3-0 \
      libnspr4 \
      libnss3 \
      libx11-xcb1 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2 \
      xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Chromium Playwright a besoin de /dev/shm suffisant côté runtime Railway
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config
COPY scripts/railway-entrypoint.sh /app/scripts/railway-entrypoint.sh

RUN chmod +x /app/scripts/railway-entrypoint.sh \
    && uv sync --frozen --no-dev \
    && uv run playwright install --with-deps chromium

EXPOSE 8080

CMD ["/app/scripts/railway-entrypoint.sh"]
