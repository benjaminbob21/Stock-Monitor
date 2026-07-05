#!/usr/bin/env bash
# Run the Stock-Monitor backend locally and expose it to the internet so a
# Vercel-hosted frontend (and your phone) can reach it.
#
#   backend:  uvicorn + in-process scheduler (RUN_SCHEDULER=1 -> one DuckDB owner,
#             so daily scans + Telegram alerts fire while the laptop is open)
#   awake:    caffeinate keeps the Mac from sleeping while it runs
#   tunnel:   TUNNEL=funnel (default, STABLE url) | TUNNEL=quick (random url)
#
# One-time prereqs (all modes):
#   cp .env.example .env          # then set API_SHARED_SECRET (openssl rand -hex 32)
#
# -- STABLE url (recommended): Tailscale Funnel --------------------------------
#   Gives a permanent https://<your-mac>.<tailnet>.ts.net URL that never changes,
#   so you set STOCK_MONITOR_API_URL in Vercel ONCE. One-time setup:
#     brew install tailscale && sudo tailscale up
#     # In the Tailscale admin console: enable MagicDNS + HTTPS certificates and
#     # add the "funnel" node attribute to this machine (Access Controls).
#   Then just run this script (TUNNEL defaults to funnel).
#
# -- Fallback: Cloudflare quick tunnel ----------------------------------------
#   TUNNEL=quick ./scripts/run-local.sh
#   Zero setup (brew install cloudflared) but the URL CHANGES every restart, so
#   you'd re-paste it into Vercel each time. Use only for a quick test.
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

: "${HOST:=127.0.0.1}"
: "${PORT:=8137}"
: "${TUNNEL:=funnel}"   # funnel (stable) | quick (random)

case "$TUNNEL" in
  funnel)
    command -v tailscale >/dev/null 2>&1 || {
      echo "tailscale not found — install it: brew install tailscale && sudo tailscale up" >&2
      echo "then enable HTTPS + the 'funnel' attribute in the Tailscale admin console." >&2
      exit 1
    }
    ;;
  quick)
    command -v cloudflared >/dev/null 2>&1 || {
      echo "cloudflared not found — install it: brew install cloudflared" >&2
      exit 1
    }
    ;;
  *)
    echo "unknown TUNNEL='$TUNNEL' (use 'funnel' or 'quick')" >&2
    exit 1
    ;;
esac

# Warn (don't block) if the soon-to-be-public API has no shared secret.
if ! grep -qE '^API_SHARED_SECRET=".+"' .env 2>/dev/null; then
  echo "WARNING: API_SHARED_SECRET looks empty in .env." >&2
  echo "         Your API will be UNAUTHENTICATED on the public tunnel — anyone with" >&2
  echo "         the URL could read positions and POST trades." >&2
  echo "         Fix: set API_SHARED_SECRET=\"\$(openssl rand -hex 32)\" in .env and put" >&2
  echo "         the SAME value in Vercel's STOCK_MONITOR_API_KEY." >&2
fi

pids=()
cleanup() {
  echo
  echo "shutting down…"
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  # Tailscale Funnel persists after this process exits; turn it off on shutdown.
  [ "$TUNNEL" = "funnel" ] && tailscale funnel "${PORT}" off 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "starting backend on http://${HOST}:${PORT} (scheduler in-process)…"
RUN_SCHEDULER=1 caffeinate -s uvicorn stock_monitor.api.app:app --host "${HOST}" --port "${PORT}" &
pids+=($!)

# Let uvicorn bind before opening the tunnel.
sleep 3

case "$TUNNEL" in
  funnel)
    echo "exposing a STABLE public URL via Tailscale Funnel…"
    echo "set this URL as STOCK_MONITOR_API_URL in Vercel (it won't change again):"
    # Serves https://<machine>.<tailnet>.ts.net -> localhost:PORT and blocks.
    tailscale funnel "${PORT}"
    ;;
  quick)
    echo "opening a RANDOM public URL via Cloudflare quick tunnel…"
    echo "copy the https URL into Vercel STOCK_MONITOR_API_URL (changes each restart):"
    cloudflared tunnel --url "http://${HOST}:${PORT}"
    ;;
esac
