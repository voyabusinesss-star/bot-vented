#!/bin/sh
# Railway : Discord/Whop + scrape + détecteur niches + fiches produit.
set -eu

echo "[railway] migrations alembic…"
uv run alembic upgrade head
echo "[railway] migrations ok"

_supervise() {
  name="$1"
  shift
  echo "[railway] démarrage $name (supervisé)"
  (
    while true; do
      "$@" || true
      echo "[railway] $name arrêté — redémarrage dans 20s"
      sleep 20
    done
  ) &
}

ENABLE_SCRAPE="${ENABLE_SCRAPE:-1}"
# Détecteur / fiches off par défaut : libère la RAM pour le scrape Discord
ENABLE_DETECTOR="${ENABLE_DETECTOR:-0}"
ENABLE_FICHES="${ENABLE_FICHES:-0}"

if [ "$ENABLE_SCRAPE" = "1" ] || [ "$ENABLE_SCRAPE" = "true" ]; then
  _supervise "scrape" uv run vinted-bot scrape --loop
else
  echo "[railway] ENABLE_SCRAPE=0 — scrape désactivé"
fi

if [ "$ENABLE_DETECTOR" = "1" ] || [ "$ENABLE_DETECTOR" = "true" ]; then
  _supervise "detector" uv run vinted-bot detector --loop
else
  echo "[railway] ENABLE_DETECTOR=0 — détecteur niches désactivé"
fi

if [ "$ENABLE_FICHES" = "1" ] || [ "$ENABLE_FICHES" = "true" ]; then
  _supervise "fiches" uv run vinted-bot fiches-produit --loop
else
  echo "[railway] ENABLE_FICHES=0 — fiches produit désactivées"
fi

echo "[railway] démarrage discord-interactions (foreground)"
exec uv run vinted-bot discord-interactions
