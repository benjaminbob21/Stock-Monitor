#!/usr/bin/env bash
# install-mac-autostart.sh — make the Stock-Monitor VM self-heal on the Mac.
#
# Installs the stockvm watchdog as a per-user LaunchAgent that runs at login and
# every 2 minutes. The watchdog starts the Multipass VM if it's stopped and
# re-applies the Tailscale Funnel if the public URL is down, so the backend
# comes back on its own after reboots and wake-from-sleep.
#
# WHY THIS SCRIPT EXISTS / the important gotcha:
#   macOS TCC blocks launchd-spawned processes from *executing* files inside the
#   privacy-protected user folders (~/Downloads, ~/Desktop, ~/Documents). This
#   repo lives in ~/Downloads, so pointing the LaunchAgent straight at
#   scripts/stockvm-watchdog.sh fails at runtime with:
#       /bin/bash: .../stockvm-watchdog.sh: Operation not permitted
#   and nothing ever self-heals. So we copy the watchdog to a NON-protected
#   location (~/Library/Application Support/stockvm) and point the agent there.
#
# Usage (on the Mac, from the repo root):
#   ./scripts/install-mac-autostart.sh
#
# Manage afterwards:
#   launchctl list | grep stockvm
#   tail -f /tmp/stockvm-autostart.log
#   launchctl bootout gui/$(id -u)/com.bob.stockvm.autostart   # stop/disable
set -euo pipefail

LABEL="com.bob.stockvm.autostart"
SRC="$(cd "$(dirname "$0")" && pwd)/stockvm-watchdog.sh"
DEST_DIR="$HOME/Library/Application Support/stockvm"
DEST="$DEST_DIR/stockvm-watchdog.sh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -f "$SRC" ]] || { echo "watchdog not found at $SRC" >&2; exit 1; }

echo "installing watchdog -> $DEST"
mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST"
chmod +x "$DEST"

echo "writing LaunchAgent -> $PLIST"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <!-- Run at login AND every 2 minutes. The interval is what covers
         wake-from-sleep: macOS does NOT re-fire RunAtLoad when you just open the
         lid, so a login-only agent would leave the VM/funnel unhealed. Each
         watchdog step is idempotent. The script lives outside ~/Downloads so
         launchd's TCC sandbox is allowed to execute it. -->
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$DEST</string>
    </array>

    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>120</integer>

    <key>StandardOutPath</key>
    <string>/tmp/stockvm-autostart.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/stockvm-autostart.log</string>
</dict>
</plist>
PLISTEOF

echo "reloading LaunchAgent…"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo
echo "done. the watchdog now runs at login and every 2 minutes."
echo "watch it with:  tail -f /tmp/stockvm-autostart.log"
