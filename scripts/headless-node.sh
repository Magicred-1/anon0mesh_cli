#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${ANONMESH_PYTHON:-$PROJECT_ROOT/venv/bin/python}"
STATE_DIR="${ANONMESH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/anonmesh}"
PID_FILE="$STATE_DIR/headless-node.pid"
LOG_FILE="$STATE_DIR/headless-node.log"
CONFIG_DIR="${ANONMESH_CONFIG_DIR:-$HOME/.reticulum}"
NETWORK="${ANONMESH_NETWORK:-devnet}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${ANONMESH_PYTHON:-python3}"
fi

node_args=(--config "$CONFIG_DIR" --network "$NETWORK")
preflight_args=(--config "$CONFIG_DIR" --network "$NETWORK")

read_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

is_running() {
  local pid command_line
  pid="$(read_pid)" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  command_line="$(ps -ww -p "$pid" -o args= 2>/dev/null)" || return 1
  [[ "$command_line" == *"$SCRIPT_DIR/exit_node.py"* ]]
}

clear_stale_pid() {
  if [[ -f "$PID_FILE" ]] && ! is_running; then
    rm -f "$PID_FILE"
  fi
}

start() {
  clear_stale_pid
  if is_running; then
    echo "headless node already running (pid $(cat "$PID_FILE"))"
    return 0
  fi

  "$PYTHON_BIN" "$SCRIPT_DIR/preflight.py" "${preflight_args[@]}"
  mkdir -p "$STATE_DIR"
  nohup "$PYTHON_BIN" "$SCRIPT_DIR/exit_node.py" "${node_args[@]}" "$@" >> "$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  sleep 1

  if ! is_running; then
    rm -f "$PID_FILE"
    echo "headless node failed to start; inspect $LOG_FILE" >&2
    return 1
  fi

  echo "headless node started (pid $(cat "$PID_FILE"))"
  echo "logs: $0 logs"
}

status() {
  clear_stale_pid
  if is_running; then
    echo "headless node running (pid $(cat "$PID_FILE"))"
    echo "logs: $LOG_FILE"
    return 0
  fi

  echo "headless node stopped"
  return 1
}

stop() {
  if ! is_running; then
    clear_stale_pid
    echo "headless node already stopped"
    return 0
  fi

  pid="$(cat "$PID_FILE")"
  kill "$pid"
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "headless node stopped"
      return 0
    fi
    sleep 0.1
  done

  echo "headless node did not stop within 2s (pid $pid)" >&2
  return 1
}

logs() {
  mkdir -p "$STATE_DIR"
  touch "$LOG_FILE"
  tail -n 100 -f "$LOG_FILE"
}

run() {
  "$PYTHON_BIN" "$SCRIPT_DIR/preflight.py" "${preflight_args[@]}"
  exec "$PYTHON_BIN" "$SCRIPT_DIR/exit_node.py" "${node_args[@]}" "$@"
}

usage() {
  cat <<EOF
Usage: $0 {preflight|run|start|status|logs|stop}

Environment:
  ANONMESH_CONFIG_DIR   Reticulum config directory (default: ~/.reticulum)
  ANONMESH_NETWORK      devnet or mainnet (default: devnet)
  ANONMESH_RPC_URL      Optional custom Solana RPC URL
  ANONMESH_PYTHON       Python executable (default: ./venv/bin/python)
  ANONMESH_STATE_DIR    PID and log directory (default: ~/.local/state/anonmesh)
EOF
}

case "${1:-}" in
  preflight) shift; exec "$PYTHON_BIN" "$SCRIPT_DIR/preflight.py" "${preflight_args[@]}" "$@" ;;
  run)       shift; run "$@" ;;
  start)     shift; start "$@" ;;
  status)    status ;;
  logs)      logs ;;
  stop)      stop ;;
  *)         usage; exit 1 ;;
esac
