# bot-vented

Bot de scraping Vinted : récupération d'annonces (titre, prix, marque, taille, état, photos, etc.).

## Stack

- Python 3.12 + uv
- Playwright
- PostgreSQL + SQLAlchemy + Alembic
- Docker / Railway (prod)

## Setup local

```bash
# Installer les dépendances
uv sync

# Copier les variables d'environnement
cp .env.example .env

# Vérifier Python
uv run python --version
```

## Commandes utiles

```bash
uv run vinted-bot
```

## Statut

Phase 0 — fondations en cours.
