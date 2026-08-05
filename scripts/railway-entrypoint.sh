#!/bin/sh
# Railway multi-services : un process par rôle (RAM isolée).
#
# APP_ROLE=
#   api       → Discord gateway + Whop webhook + migrations (HTTP /health)
#   scrape    → scrape public + filtres privés (Playwright)
#   detector  → détecteur de niches
#   niches    → detector puis fiches à tour de rôle (plan free, 1 Chromium)
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
    # Plan free : TOUJOURS detector d'abord (fenêtre ~35 min), puis 1 tentative fiche.
    # 1 Chromium à la fois — scrape public+privé reste sur APP_ROLE=scrape (intact).
    # Cadence cible : ~10 détections Discord / h (cap code) + 1 fiche / h (cooldown).
    DETECTOR_WINDOW_S="${NICHES_DETECTOR_WINDOW_SECONDS:-2100}"   # 35 min
    CYCLE_PAUSE_S="${NICHES_DETECTOR_CYCLE_PAUSE_SECONDS:-180}" # 3 min entre cycles
    PHASE_PAUSE_S="${NICHES_PHASE_PAUSE_SECONDS:-60}"
    # Deep-dive court pour laisser de la RAM/temps au detector (env override OK)
    export FICHES_DEVELOP_SECONDS="${FICHES_DEVELOP_SECONDS:-900}"
    echo "[railway] niches: detector ${DETECTOR_WINDOW_S}s → fiche (develop=${FICHES_DEVELOP_SECONDS}s)"
    while true; do
      echo "[railway] niches: PHASE DETECTOR (fenêtre ${DETECTOR_WINDOW_S}s)"
      deadline=$(( $(date +%s) + DETECTOR_WINDOW_S ))
      cycle=0
      while [ "$(date +%s)" -lt "$deadline" ]; do
        cycle=$((cycle + 1))
        echo "[railway] niches: detector cycle ${cycle}"
        uv run vinted-bot detector --once || true
        now=$(date +%s)
        left=$((deadline - now))
        if [ "$left" -le 0 ]; then
          break
        fi
        pause="$CYCLE_PAUSE_S"
        if [ "$left" -lt "$pause" ]; then
          pause="$left"
        fi
        echo "[railway] niches: pause detector ${pause}s (Chromium relâché)"
        sleep "$pause"
      done
      echo "[railway] niches: PHASE FICHES (1 cycle, niches déjà postées par detector)"
      uv run vinted-bot fiches-produit --once || true
      echo "[railway] niches: pause ${PHASE_PAUSE_S}s avant retour detector"
      sleep "$PHASE_PAUSE_S"
    done
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
