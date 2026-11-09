#!/bin/bash
# Install the ISP Picker helper as a per-user launchd agent (starts at login, restarts if it stops).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$(command -v python3)}"
LABEL="${ISP_PICKER_LABEL:-local.isp-picker}"
HOME_DIR="${ISP_PICKER_HOME:-$HOME/.config/isp-picker}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/isp-picker.log"

mkdir -p "$HOME_DIR" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# Phone agent (runs on an Android phone over adb). Build it if it is missing or older than its source.
AGENT="$DIR/agent/build/agent-arm64"
if [ ! -f "$AGENT" ] || [ "$DIR/agent/main.go" -nt "$AGENT" ]; then
  bash "$DIR/scripts/build-agent.sh" || echo "Skipping the phone agent: phone (adb) mode will not be available."
fi
# Only the keys you change belong in config.json; everything else comes from the defaults in the helper.
[ -f "$HOME_DIR/config.json" ] || printf '{}\n' > "$HOME_DIR/config.json"

cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/helper/isp_picker.py</string>
    <string>serve</string>
    <string>--config</string>
    <string>$HOME_DIR/config.json</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ISP_PICKER_HOME</key><string>$HOME_DIR</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
if curl -s -m 3 "http://127.0.0.1:$("$PY" -c "import json;print(json.load(open('$HOME_DIR/config.json')).get('control_port',10872))")/status" >/dev/null; then
  echo "OK: helper is running ($LABEL). Log: $LOG"
else
  echo "Helper did not answer yet. Check the log: $LOG"
fi
