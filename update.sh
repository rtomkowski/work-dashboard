#!/bin/bash
# Deploys this folder to the running dashboard. Keeps your config (adds new keys only)
# and keeps the WorkDashboard.app wrapper. Safe to re-run.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/WorkDashboard"
LABEL="local.workdashboard"
[ -x "$APP/.venv/bin/python" ] || { echo "Dashboard not installed yet. Run install.sh first."; exit 1; }

cp "$SRC/harvest.py" "$SRC/notes.py" "$SRC/alarms.py" "$SRC/server.py" "$SRC/stats.py" "$SRC/ahead.py" "$SRC/planner.py" "$SRC/outlook.py" "$SRC/dashboard.html" "$SRC/auth.py" "$APP/"
# Merge config: your existing values win; new keys are added; sync window widened for Month view
"$APP/.venv/bin/python" - "$SRC/config.example.json" "$APP/config.json" <<'PY'
import json, sys
new = json.load(open(sys.argv[1]))
try:
    cur = json.load(open(sys.argv[2]))
except FileNotFoundError:
    cur = {}
def merge(base, mine):
    for k, v in base.items():
        if k not in mine:
            mine[k] = v
        elif isinstance(v, dict) and isinstance(mine[k], dict):
            merge(v, mine[k])
    return mine
out = merge(new, cur)
# old default only fetched issues assigned to you; empty = new default (all open KAN issues)
if out.get("jira", {}).get("jql", "").startswith("assignee = currentUser() AND statusCategory != Done"):
    out["jira"]["jql"] = ""
# v0.45: weekly reviews go to Inbox/WorkDashboard unless you picked a folder
if not out.get("obsidian", {}).get("review_folder"):
    out["obsidian"]["review_folder"] = "Inbox/WorkDashboard"
out["calendar"]["days_back"] = max(out["calendar"].get("days_back", 0), 35)
out["calendar"]["days_ahead"] = max(out["calendar"].get("days_ahead", 0), 70)
json.dump(out, open(sys.argv[2], "w"), indent=2, ensure_ascii=False)
print("Config merged:", sys.argv[2])
PY

launchctl kickstart -k "gui/$(id -u)/$LABEL" && echo "Dashboard restarted."
sleep 3
open "$("$APP/.venv/bin/python" "$APP/auth.py" link --local 2>/dev/null || echo http://127.0.0.1:8765/)"  # KAN-42 access token
echo "Other devices: run  \"$APP/.venv/bin/python\" \"$APP/auth.py\" show"