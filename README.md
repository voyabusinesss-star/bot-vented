# bot-vented

Bot de scraping Vinted : récupération d'annonces (titre, prix, marque, taille, état, photos, etc.).

## Stack

- Python 3.12 + uv
- Playwright
- PostgreSQL + SQLAlchemy + Alembic
- Docker Compose (Postgres local) / Railway (prod)

## Setup local

```bash
# Dépendances
uv sync
cp .env.example .env

# Postgres (nécessite Docker Desktop)
docker compose up -d

# Migrations
uv run alembic upgrade head

# Vérifications
uv run vinted-bot db-check
uv run vinted-bot db-seed
```

## Commandes utiles

```bash
uv run vinted-bot --help
uv run vinted-bot hello
uv run vinted-bot db-check
uv run vinted-bot db-seed
uv run vinted-bot scrape --query "nike" --once --max-items 10
uv run pytest
```

## Structure

```text
src/vinted_bot/
  main.py          # CLI
  config.py        # variables d'environnement
  clients/         # Playwright / HTTP
  parsers/         # HTML/JSON → données
  services/        # orchestration métier
  db/              # modèles, session, repositories
  jobs/            # planification
  utils/           # logs, retry, rate limit
tests/
alembic/           # migrations
docker-compose.yml # Postgres local
```

## Statut

Phase 2 — scrape recherche Playwright → parse → upsert DB.
