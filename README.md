# bot-vented

Bot de scraping Vinted : récupération d'annonces (titre, prix, marque, taille, état, photos, etc.).

## Stack

- Python 3.12 + uv
- Playwright
- PostgreSQL + SQLAlchemy + Alembic
- Discord (auto-post par marque)
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

## Discord — setup manuel

1. Créer un serveur Discord (ex. `Vintify Alerts`)
2. Créer les canaux marque : `#nike`, `#adidas`, … (seulement les marques suivies)
3. Créer un salon regroupement (ex. `#toutes-annonces`) — reçoit une copie de chaque post marque
4. Créer `#logs` (optionnel) pour les résumés de scrape
5. Créer le bot sur [Discord Developer Portal](https://discord.com/developers/applications), inviter avec permissions Send Messages / Embed Links / View Channels
6. Mode développeur ON → copier les IDs de canaux
7. Remplir `.env` :

```env
DISCORD_ENABLED=true
DISCORD_BOT_TOKEN=ton_token
DISCORD_CHANNEL_NIKE=...
DISCORD_CHANNEL_ADIDAS=...
DISCORD_CHANNEL_ALL=...
DISCORD_CHANNEL_LOGS=...
```

Les marques **sans** canal dédié sont ignorées (ni DB, ni Discord).

8. Tester :

```bash
uv run vinted-bot discord-test
uv run vinted-bot scrape --query "nike" --once --max-items 5
```

## Commandes utiles

```bash
uv run vinted-bot --help
uv run vinted-bot hello
uv run vinted-bot db-check
uv run vinted-bot db-seed
uv run vinted-bot discord-test
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
  notify/          # Discord embeds
  db/              # modèles, session, repositories
  jobs/            # planification
  utils/           # logs, retry, rate limit
tests/
alembic/           # migrations
docker-compose.yml # Postgres local
```

## Statut

Phase Discord — auto-post des annonces scrapées vers canaux par marque.
