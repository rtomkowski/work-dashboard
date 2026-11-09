#!/bin/bash
# Stops Work Dashboard and removes it from login items.
#   ./uninstall.sh           keeps your settings and data (~/Library/Application Support/WorkDashboard)
#   ./uninstall.sh --purge   also deletes settings, data, the app wrapper and the Keychain entries
# Nothing in your calendars, reminders, Obsidian vault or Jira is touched.
LABEL="local.workdashboard"
APP="$HOME/Library/Application Support/WorkDashboard"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "$APP/server.py" 2>/dev/null || true
echo "Work Dashboard stopped and removed from login."

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$APP" "$HOME/Applications/WorkDashboard.app"
  security delete-generic-password -s WorkDashboard-access >/dev/null 2>&1 || true
  while security delete-generic-password -s work-dashboard-jira >/dev/null 2>&1; do :; done
  echo "Settings, data, app wrapper and Keychain entries deleted."
  echo "If you used Tailscale sharing:  tailscale serve reset"
else
  echo "Settings and data kept in: $APP  (./uninstall.sh --purge deletes them)"
fi
