#!/usr/bin/env bash
# Sync the DuckDB database + trained models between your two laptops over Tailscale,
# so whichever machine you fail over to has the same history / positions / models.
#
# WHY THIS IS MANUAL: DuckDB is a single-file database with a write-ahead log.
# Copying it while a backend is WRITING can produce a corrupt copy. So the safe
# routine is:
#   1. Stop the backend on BOTH machines (Ctrl-C the run-local.sh).
#   2. Run this FROM the machine that currently has the good/latest data.
#
# Prereqs:
#   - Both machines on the same tailnet (`tailscale up`).
#   - SSH reachable between them. Easiest: Tailscale SSH -> run `sudo tailscale up --ssh`
#     on BOTH machines. (Or enable macOS "Remote Login" in System Settings > General
#     > Sharing.)
#
# Usage:
#   ./scripts/sync-data.sh <remote-tailscale-host> [push|pull]
#     push (default): send THIS machine's data+models TO the remote (remote = backup)
#     pull          : bring the remote's data+models TO this machine
#
# Env overrides:
#   REMOTE_USER  (default: $USER)                     ssh user on the remote
#   REMOTE_PATH  (default: ~/Downloads/Stock-Monitor) repo path on the remote
#
# Examples:
#   ./scripts/sync-data.sh intel-macbook-pro push        # push to the backup laptop
#   REMOTE_USER=bob ./scripts/sync-data.sh intel-macbook-pro pull
set -euo pipefail

cd "$(dirname "$0")/.."

REMOTE_HOST="${1:-}"
DIRECTION="${2:-push}"
REMOTE_USER="${REMOTE_USER:-$USER}"
REMOTE_PATH="${REMOTE_PATH:-~/Downloads/Stock-Monitor}"

if [ -z "$REMOTE_HOST" ]; then
  echo "usage: $0 <remote-tailscale-host> [push|pull]" >&2
  exit 1
fi

# Refuse to run if a backend here is holding the DB open (would sync a hot file).
if command -v lsof >/dev/null 2>&1 && lsof data/stock_monitor.duckdb >/dev/null 2>&1; then
  echo "ERROR: data/stock_monitor.duckdb is open — a backend is running on THIS machine." >&2
  echo "       Stop ./scripts/run-local.sh first, then re-run this sync." >&2
  exit 1
fi

remote="${REMOTE_USER}@${REMOTE_HOST}"
opts=(-avh --progress)

echo "Reminder: make sure the backend is STOPPED on the remote too, or you may copy a hot DB."

case "$DIRECTION" in
  push)
    echo "PUSH: this machine -> ${remote}:${REMOTE_PATH}"
    rsync "${opts[@]}" data/   "${remote}:${REMOTE_PATH}/data/"
    rsync "${opts[@]}" models/ "${remote}:${REMOTE_PATH}/models/"
    ;;
  pull)
    echo "PULL: ${remote}:${REMOTE_PATH} -> this machine"
    rsync "${opts[@]}" "${remote}:${REMOTE_PATH}/data/"   data/
    rsync "${opts[@]}" "${remote}:${REMOTE_PATH}/models/" models/
    ;;
  *)
    echo "unknown direction '$DIRECTION' (use push or pull)" >&2
    exit 1
    ;;
esac

echo "sync complete."
