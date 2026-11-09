#!/bin/bash
# Stop the helper and move its launchd agent to the Trash. Config in ~/.config/isp-picker is kept.
set -euo pipefail
LABEL="${ISP_PICKER_LABEL:-local.isp-picker}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
[ -f "$PLIST" ] && trash "$PLIST"
echo "Helper stopped and removed from login items."
