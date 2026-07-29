#!/usr/bin/env bash
# stockvm-watchdog.sh — keep the Stock-Monitor backend reachable from the Mac.
#
# Run periodically by the com.bob.stockvm.autostart LaunchAgent (StartInterval).
# It heals the two things that break when the laptop sleeps/wakes:
#   1. the Multipass VM being paused/stopped   -> `multipass start`
#   2. the Tailscale Funnel serve config being dropped on VM resume
#      (uvicorn keeps running so systemd still says "active", but the public
#       https URL goes dead) -> re-apply the funnel, or restart the service.
#
# Safe to run every couple of minutes: every step is idempotent and it only
# takes corrective action when a health check actually fails.
set -uo pipefail

VM="${STOCKVM_NAME:-stockvm}"
PORT="${STOCKVM_PORT:-8137}"
PUBLIC_URL="${STOCKVM_PUBLIC_URL:-https://stockvm.tailfd4d8c.ts.net}"
MULTIPASS="${MULTIPASS_BIN:-/usr/local/bin/multipass}"
LOG_TAG="[stockvm-watchdog $(date '+%Y-%m-%d %H:%M:%S')]"

log() { echo "$LOG_TAG $*"; }

command -v "$MULTIPASS" >/dev/null 2>&1 || MULTIPASS="$(command -v multipass || true)"
[ -n "$MULTIPASS" ] || { log "multipass not found"; exit 0; }

# 1) Make sure the VM is running (no-op if it already is).
state="$("$MULTIPASS" info "$VM" 2>/dev/null | awk -F': *' '/^State:/{print $2}')"
if [ "$state" != "Running" ]; then
  log "VM state='$state' -> starting"
  for i in 1 2 3 4 5; do "$MULTIPASS" start "$VM" >/dev/null 2>&1 && break; sleep 6; done
fi

# 2) Public health check — this is what Vercel actually hits.
pub="$(curl -s --max-time 15 -o /dev/null -w '%{http_code}' "$PUBLIC_URL/health" 2>/dev/null)"
if [ "$pub" = "200" ]; then
  exit 0
fi
log "public health=$pub -> investigating"

# 3) Is the backend alive inside the VM?
loc="$("$MULTIPASS" exec "$VM" -- curl -s --max-time 8 -o /dev/null -w '%{http_code}' \
       "http://127.0.0.1:$PORT/health" 2>/dev/null)"
log "local health=$loc"

if [ "$loc" != "200" ]; then
  # Backend itself is down/booting — restart the service (re-runs run-linux.sh,
  # which re-establishes the --bg funnel too).
  log "backend down -> restarting stock-monitor service"
  "$MULTIPASS" exec "$VM" -- sudo systemctl restart stock-monitor >/dev/null 2>&1
else
  # Backend is fine, only the public funnel dropped -> re-apply it cheaply
  # (no FinBERT reload). `serve reset` clears any stale serve/funnel config and
  # `--bg` persists the fresh config in tailscaled. We intentionally do NOT call
  # `tailscale funnel <port> off` here: that syntax was removed in newer
  # Tailscale ("the CLI for serve and funnel has changed") and `serve reset`
  # already does the teardown.
  #
  # Both commands run in a SINGLE `multipass exec … sudo bash -c` on purpose:
  # issuing them as two separate `multipass exec` calls hangs, because the
  # backgrounded `funnel --bg` process inherits the exec's stdout pipe and keeps
  # it open forever. Redirecting its std streams to /dev/null lets it detach.
  log "backend up but funnel down -> re-applying funnel"
  "$MULTIPASS" exec "$VM" -- sudo bash -c \
    "tailscale serve reset >/dev/null 2>&1; tailscale funnel --bg $PORT </dev/null >/dev/null 2>&1" \
    >/dev/null 2>&1
fi

# 4) Re-check and report. Re-applying a funnel takes ~10-15s to propagate to the
# public Tailscale relay, so wait long enough that this reading is truthful
# rather than a premature "still down".
sleep 15
pub="$(curl -s --max-time 15 -o /dev/null -w '%{http_code}' "$PUBLIC_URL/health" 2>/dev/null)"
log "public health after heal=$pub"
