#!/usr/bin/env bash
# Installs the panel as a user service: launchd (macOS) or systemd --user (Linux).
#
#   ./scripts/install-service.sh            installs and (re)starts
#   ./scripts/install-service.sh --remove   removes the service
#
# On Windows the equivalent is scripts/install-service.ps1 (Scheduled Task).
#
# Why a system service: launched from a terminal (or an agent session),
# the process terminates with it -- the display keeps showing on the phone,
# but touch actions silently stop working. See docs/service.md.

set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$(command -v python3 || command -v python)"
SUPERVISOR="$BASE/service.py"
LABEL="com.mcp-display-companion"
CONFIG="${PANEL_COMPANION_CONFIG:-config.json}"
LOG_DIR="$BASE/logs"

if [[ "${1:-}" == "--remove" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    launchctl unload "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
    echo "agent $LABEL removed."
  else
    systemctl --user disable --now mcp-display-companion.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/mcp-display-companion.service"
    systemctl --user daemon-reload || true
    echo "unit mcp-display-companion.service removed."
  fi
  exit 0
fi

if [[ ! -d "$BASE/mcp_display_companion" ]]; then
  echo "run from within the repository (could not find $BASE/mcp_display_companion)" >&2
  exit 1
fi
mkdir -p "$LOG_DIR"

if [[ "$(uname -s)" == "Darwin" ]]; then
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SUPERVISOR</string>
    <string>--config</string>
    <string>$CONFIG</string>
  </array>
  <key>WorkingDirectory</key><string>$BASE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/launchd.err.log</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "agent $LABEL loaded ($PLIST)."
  echo "logs: $LOG_DIR/launchd.out.log · status: launchctl list | grep $LABEL"
else
  UNIT="$HOME/.config/systemd/user/mcp-display-companion.service"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=Panel Companion (MCP Display)
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$BASE
ExecStart="$PYTHON" "$SUPERVISOR" --config "$CONFIG"
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/systemd.log
StandardError=append:$LOG_DIR/systemd.log

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now mcp-display-companion.service
  echo "unit mcp-display-companion.service active."
  echo "logs: $LOG_DIR/systemd.log · status: systemctl --user status mcp-display-companion"
fi

echo
echo "quick check:"
echo "  tail -n 5 $BASE/companion.log     # should show 'Heartbeat: active' every 60 s"
