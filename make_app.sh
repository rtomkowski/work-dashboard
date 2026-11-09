#!/bin/bash
# Wraps the dashboard server in a small background app (no Dock icon) so macOS
# can grant it Calendar and Reminders access. Run after install.sh.
# Re-run it whenever you re-run install.sh (install.sh resets the LaunchAgent).
set -euo pipefail

APP="$HOME/Library/Application Support/WorkDashboard"
BUNDLE="$HOME/Applications/WorkDashboard.app"
LABEL="local.workdashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -x "$APP/.venv/bin/python" ] || { echo "Run install.sh first."; exit 1; }

# Stop the old python-only service
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
pkill -f "$APP/server.py" 2>/dev/null || true

# Launcher the app runs
cat > "$APP/run.sh" <<EOF
#!/bin/bash
cd "$APP"
exec ./.venv/bin/python server.py >> logs/out.log 2>> logs/err.log
EOF
chmod +x "$APP/run.sh"

# Build the app bundle
mkdir -p "$HOME/Applications"
rm -rf "$BUNDLE"
osacompile -o "$BUNDLE" -e "do shell script quoted form of \"$APP/run.sh\""

PL="$BUNDLE/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string "$LABEL.app" "$PL"
plutil -replace CFBundleName -string "WorkDashboard" "$PL"
plutil -replace LSUIElement -bool true "$PL"
MSG="Shows your calendar and reminders on your local work dashboard."
for k in NSCalendarsUsageDescription NSCalendarsFullAccessUsageDescription \
         NSRemindersUsageDescription NSRemindersFullAccessUsageDescription; do
  plutil -replace "$k" -string "$MSG" "$PL"
done
codesign --force --deep -s - "$BUNDLE"

# LaunchAgent now starts the app instead of bare python
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$BUNDLE/Contents/MacOS/applet</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
</dict></plist>
EOF

launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "WorkDashboard app started. Approve the Calendar and Reminders prompts now."
echo "If no prompt appears within ~30 s: System Settings > Privacy & Security > Calendars / Reminders > enable WorkDashboard."
sleep 5
open "http://127.0.0.1:8765/"
