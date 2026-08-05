# Discord interactions + webhook Whop (Railway)
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    WHOP_WEBHOOK_HOST=0.0.0.0 \
    WHOP_WEBHOOK_PORT=8080

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

EXPOSE 8080

CMD ["uv", "run", "vinted-bot", "discord-interactions"]
