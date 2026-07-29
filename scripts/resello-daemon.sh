#!/usr/bin/env bash
# Lance detector + fiches en arrière-plan (survit à la fermeture du terminal).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PID_DIR="$ROOT/.data/pids"
LOG_DIR="$ROOT/.data/logs"
DETECTOR_PID="$PID_DIR/detector.pid"
FICHES_PID="$PID_DIR/fiches.pid"
DETECTOR_LOG="$LOG_DIR/detector.log"
FICHES_LOG="$LOG_DIR/fiches.log"

mkdir -p "$PID_DIR" "$LOG_DIR"

_is_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_stop_pidfile() {
  local name="$1"
  local pidfile="$2"
  if [[ ! -f "$pidfile" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if _is_running "$pid"; then
    echo "Arrêt $name (pid $pid)…"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      _is_running "$pid" || break
      sleep 0.5
    done
    if _is_running "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pidfile"
}

_stop_by_pattern() {
  local pattern="$1"
  pkill -f "$pattern" 2>/dev/null || true
}

cmd_start() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv introuvable — installe uv ou active ton PATH."
    exit 1
  fi

  cmd_stop

  echo "Démarrage detector --loop → $DETECTOR_LOG"
  nohup uv run vinted-bot detector --loop >>"$DETECTOR_LOG" 2>&1 &
  echo $! >"$DETECTOR_PID"

  echo "Démarrage fiches-produit --loop → $FICHES_LOG"
  nohup uv run vinted-bot fiches-produit --loop >>"$FICHES_LOG" 2>&1 &
  echo $! >"$FICHES_PID"

  sleep 1
  cmd_status
  echo ""
  echo "OK — les bots tournent en arrière-plan."
  echo "Fermer le terminal ne les arrête pas."
  echo "Logs : tail -f .data/logs/detector.log .data/logs/fiches.log"
}

cmd_stop() {
  _stop_pidfile "detector" "$DETECTOR_PID"
  _stop_pidfile "fiches" "$FICHES_PID"
  _stop_by_pattern "vinted-bot detector --loop"
  _stop_by_pattern "vinted-bot fiches-produit --loop"
  echo "Bots arrêtés."
}

cmd_status() {
  local dpid fpid
  dpid="$(cat "$DETECTOR_PID" 2>/dev/null || true)"
  fpid="$(cat "$FICHES_PID" 2>/dev/null || true)"
  if _is_running "$dpid"; then
    echo "detector      RUNNING  pid=$dpid  log=$DETECTOR_LOG"
  else
    echo "detector      STOPPED"
  fi
  if _is_running "$fpid"; then
    echo "fiches-produit RUNNING  pid=$fpid  log=$FICHES_LOG"
  else
    echo "fiches-produit STOPPED"
  fi
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

usage() {
  cat <<EOF
Usage: $(basename "$0") {start|stop|status|restart}

  start    — detector + fiches en arrière-plan (nohup)
  stop     — arrête les deux
  status   — affiche l'état
  restart  — stop puis start
EOF
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart) cmd_restart ;;
  *) usage; exit 1 ;;
esac
