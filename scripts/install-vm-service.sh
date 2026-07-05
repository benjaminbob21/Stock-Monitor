#!/usr/bin/env bash
# Install Stock-Monitor as a systemd service inside the Linux VM.
#
# This is the "run once and forget" piece: after this, the backend + Tailscale
# Funnel start automatically on boot and auto-restart if they ever crash — you
# never have to run terminal commands again to keep the site live. The in-app
# "Refresh data" button then handles on-demand scans, and the in-process
# scheduler handles the daily scan.
#
# Usage (inside the VM, from the repo root):
#   ./scripts/install-vm-service.sh
#
# Manage it afterwards:
#   sudo systemctl status  stock-monitor
#   sudo systemctl restart stock-monitor
#   sudo systemctl stop    stock-monitor
#   journalctl -u stock-monitor -f          # live logs
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
SERVICE=/etc/systemd/system/stock-monitor.service

if [[ ! -x "${REPO_DIR}/scripts/run-linux.sh" ]]; then
  chmod +x "${REPO_DIR}/scripts/run-linux.sh"
fi

echo "installing ${SERVICE}"
echo "  repo:  ${REPO_DIR}"
echo "  user:  ${RUN_USER}"

sudo tee "${SERVICE}" >/dev/null <<UNIT
[Unit]
Description=Stock-Monitor backend + Tailscale Funnel
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/scripts/run-linux.sh
Restart=always
RestartSec=5
# Give torch/FinBERT time to load models on first start.
TimeoutStartSec=180
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT

echo "reloading systemd + enabling on boot…"
sudo systemctl daemon-reload
sudo systemctl enable stock-monitor
sudo systemctl restart stock-monitor

echo
echo "done. the backend now starts on boot and restarts on crash."
echo "check it with:"
echo "  sudo systemctl status stock-monitor --no-pager"
echo "  journalctl -u stock-monitor -f"
