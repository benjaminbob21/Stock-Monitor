#!/usr/bin/env bash
# Linux runner for the Stock-Monitor backend — e.g. inside a Multipass Ubuntu VM on
# an Intel Mac, where FinBERT/torch installs cleanly (Linux x86-64 has wheels).
# Mirrors scripts/run-local.sh but drops macOS-only `caffeinate` and uses Tailscale
# Funnel for a stable public URL.
#
#   backend: uvicorn + in-process scheduler (RUN_SCHEDULER=1 -> one DuckDB owner)
#   tunnel:  Tailscale Funnel -> stable https://<vm-name>.<tailnet>.ts.net
#
# One-time prereqs (inside the VM):
#   sudo apt install -y python3.12-venv build-essential libgomp1 git curl
#   python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev,finbert]"
#   cp .env.example .env   # set SENTIMENT_BACKEND=finbert + the SAME API_SHARED_SECRET
#   curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
#   sudo tailscale set --operator="$USER"   # lets you run `tailscale funnel` without sudo
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

: "${HOST:=127.0.0.1}"
: "${PORT:=8137}"

command -v tailscale >/dev/null 2>&1 || {
  echo "tailscale not found — install it:" >&2
  echo "  curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up" >&2
  exit 1
}

# Warn (don't block) if the soon-to-be-public API has no shared secret.
if ! grep -qE '^API_SHARED_SECRET=".+"' .env 2>/dev/null; then
  echo "WARNING: API_SHARED_SECRET looks empty in .env." >&2
  echo "         The public tunnel would be UNAUTHENTICATED — anyone with the URL could" >&2
  echo "         read positions and POST trades. Set it (openssl rand -hex 32) and match" >&2
  echo "         it in Vercel's STOCK_MONITOR_API_KEY." >&2
fi

pids=()
cleanup() {
  echo
  echo "shutting down…"
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  tailscale funnel "${PORT}" off 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "starting backend on http://${HOST}:${PORT} (scheduler in-process)…"
RUN_SCHEDULER=1 uvicorn stock_monitor.api.app:app --host "${HOST}" --port "${PORT}" &
pids+=($!)

# Let uvicorn bind before opening the tunnel.
sleep 3

echo "exposing a STABLE public URL via Tailscale Funnel…"
echo "add this URL to Vercel STOCK_MONITOR_API_URL (comma-separated with your other backend):"
tailscale funnel "${PORT}"
