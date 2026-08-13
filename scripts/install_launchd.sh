#!/usr/bin/env bash
# Installs a macOS LaunchAgent that runs `brain reminders tick` every 60s
# (plan.md §11). Idempotent: re-running replaces the previous install.
set -euo pipefail

LABEL="com.heybrain.reminders.tick"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$HOME/.heybrain/logs"

BRAIN_BIN="$(command -v brain || true)"
if [[ -z "$BRAIN_BIN" ]]; then
    echo "error: 'brain' not found on PATH. Install heyBrain first (pip install -e .)." >&2
    exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BRAIN_BIN</string>
        <string>reminders</string>
        <string>tick</string>
    </array>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/reminders-tick.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/reminders-tick.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Installed $LABEL -- runs 'brain reminders tick' every 60s."
echo "Logs: $LOG_DIR/reminders-tick.log"
echo "Uninstall with: launchctl unload $PLIST_PATH && rm $PLIST_PATH"
