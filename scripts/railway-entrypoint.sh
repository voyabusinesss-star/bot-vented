#!/bin/sh
# Railway : Discord interactions + webhook Whop + scrape Vinted (même service).
set -eu

ENABLE_SCRAPE="${ENABLE_SCRAPE:-1}"

if [ "$ENABLE_SCRAPE" = "1" ] || [ "$ENABLE_SCRAPE" = "true" ]; then
  echo "[railway] démarrage scrape --loop (supervisé)"
  (
    while true; do
      uv run vinted-bot scrape --loop || true
      echo "[railway] scrape arrêté — redémarrage dans 15s"
      sleep 15
    done
  ) &
else
  echo "[railway] ENABLE_SCRAPE=0 — scrape désactivé"
fi

echo "[railway] démarrage discord-interactions (foreground)"
exec uv run vinted-bot discord-interactions
