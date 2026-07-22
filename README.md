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

1. Créer un serveur Discord + canaux marque (ex. catégorie `les-classiques`)
2. Créer `#all-vetement` (salon regroupement)
3. Créer le bot, l'inviter (Send Messages / Embed Links / View Channels)
4. Mode développeur ON → copier les **IDs numériques** des salons
5. Remplir `.env` (`DISCORD_CHANNEL_*`) — voir `.env.example` pour la liste complète
6. Tester :

```bash
uv run vinted-bot discord-test
uv run vinted-bot scrape --query "nike" --once --max-items 5
```

Seules les marques avec un ID renseigné sont scrapées/postées.

## Commandes utiles

```bash
uv run vinted-bot --help
uv run vinted-bot hello
uv run vinted-bot db-check
uv run vinted-bot db-seed
uv run vinted-bot discord-test
uv run vinted-bot scrape --query "nike" --once --max-items 10
uv run vinted-bot scrape --all --once --max-items 5
uv run vinted-bot scrape --loop
uv run pytest
```

Les recherches se configurent dans [`config/searches.yaml`](config/searches.yaml) (query + marque + filtres optionnels), les IDs Discord restent dans `.env`.
Mode 24/7 : `scrape --loop` (intervalle / restart navigateur dans le YAML).

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
