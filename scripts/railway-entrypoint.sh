#!/bin/sh
# Railway multi-services : un process par rôle (RAM isolée).
#
# APP_ROLE=
#   api       → Discord gateway + Whop webhook + migrations (HTTP /health)
#   scrape    → scrape public + filtres privés (Playwright)
#   detector  → détecteur de niches
#   fiches    → fiches produit niches
#   all       → legacy mono-service (déconseillé)
set -eu

ROLE="${APP_ROLE:-api}"

echo "[railway] APP_ROLE=${ROLE}"

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

_migrate() {
  echo "[railway] migrations alembic…"
  uv run alembic upgrade head
  echo "[railway] migrations ok"
}

case "$ROLE" in
  api)
    _migrate
    echo "[railway] démarrage discord-interactions (foreground)"
    exec uv run vinted-bot discord-interactions
    ;;

  scrape)
    # Pas de HTTP public requis — boucle scrape seule
    echo "[railway] démarrage scrape public+privé (foreground)"
    exec uv run vinted-bot scrape --loop
    ;;

  detector)
    echo "[railway] démarrage detector niches (foreground)"
    exec uv run vinted-bot detector --loop
    ;;

  fiches)
    echo "[railway] démarrage fiches produit (foreground)"
    exec uv run vinted-bot fiches-produit --loop
    ;;

  niches)
    # Plan free : detector + fiches sur un seul service (sans scrape Discord)
    echo "[railway] démarrage detector + fiches (supervisés)"
    _supervise "detector" uv run vinted-bot detector --loop
    echo "[railway] démarrage fiches produit (foreground)"
    exec uv run vinted-bot fiches-produit --loop
    ;;

  all)
    # Legacy : tout dans un container (risque OOM)
    _migrate
    ENABLE_SCRAPE="${ENABLE_SCRAPE:-1}"
    ENABLE_DETECTOR="${ENABLE_DETECTOR:-0}"
    ENABLE_FICHES="${ENABLE_FICHES:-0}"
    if [ "$ENABLE_SCRAPE" = "1" ] || [ "$ENABLE_SCRAPE" = "true" ]; then
      _supervise "scrape" uv run vinted-bot scrape --loop
    fi
    if [ "$ENABLE_DETECTOR" = "1" ] || [ "$ENABLE_DETECTOR" = "true" ]; then
      _supervise "detector" uv run vinted-bot detector --loop
    fi
    if [ "$ENABLE_FICHES" = "1" ] || [ "$ENABLE_FICHES" = "true" ]; then
      _supervise "fiches" uv run vinted-bot fiches-produit --loop
    fi
    echo "[railway] démarrage discord-interactions (foreground)"
    exec uv run vinted-bot discord-interactions
    ;;

  *)
    echo "[railway] APP_ROLE inconnu: ${ROLE} (api|scrape|detector|fiches|niches|all)" >&2
    exit 1
    ;;
esac
