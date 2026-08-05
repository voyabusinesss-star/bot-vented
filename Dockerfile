# Discord interactions + webhook Whop (Railway)
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

# Railway injecte PORT ; le webhook Whop écoute dessus
EXPOSE 8080

CMD ["sh", "-c", "uv run alembic upgrade head && uv run vinted-bot discord-interactions"]
