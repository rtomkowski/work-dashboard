# Contributing

Thanks for wanting to improve Work Dashboard. Bug reports, ideas and pull requests are all welcome.

## Ground rules

- **Local first.** Calendar, reminder and note content never leaves the Mac. No cloud services, no analytics. The only outbound calls are Jira (when turned on) and the weather coordinates.
- **Few dependencies.** Python standard library plus `pyobjc` for EventKit. The page is one HTML file with plain JavaScript, no build step.
- **Careful with writes.** The dashboard changes data only in the narrow ways listed under "What the dashboard may change" in the README (create-only notes, its own time blocks and reminders with undo, Jira status moves). New write features should follow the same pattern: create-only or reversible, never silent edits or deletes.
- **Optional parts stay optional.** Everything must work with only Calendar and Reminders; Obsidian, Jira, Outlook, weather and Tailscale are each switched by config.

## Getting started

```bash
git clone https://github.com/rtomkowski/work-dashboard.git && cd work-dashboard
python3 demo/serve_demo.py        # http://127.0.0.1:8766, made-up data, no permissions needed
```

The demo serves the real `dashboard.html`, so most page work can be done and tested there. To run your changes against your own data: `./install.sh` once, then `./update.sh` after each change.

| File | What it does |
|---|---|
| `harvest.py` | Reads Calendar and Reminders (EventKit), Obsidian and Jira into one JSON snapshot |
| `server.py` | Local web server on 127.0.0.1:8765, API for the page, background refresh |
| `dashboard.html` | The whole page (HTML, CSS, JavaScript) |
| `stats.py`, `ahead.py`, `planner.py` | Statistics, 8-week forecast, time-block suggestions |
| `notes.py` | Create-only Obsidian notes, capture, weekly review export |
| `outlook.py` | Optional Outlook bridge (AppleScript) |
| `auth.py` | Access token (Keychain) |
| `demo/` | Demo server, made-up data, screenshot scripts |

## Pull requests

1. One topic per pull request, with a short description of what and why.
2. Test in the demo, and with real data if your change touches `harvest.py` or `server.py`.
3. Screenshots only from the demo, never from your own calendar.
4. Add a line to `CHANGELOG.md`.

## Reporting a bug

Include macOS version, Python version (`python3 --version`), what you expected and what happened, and the last lines of `~/Library/Application Support/WorkDashboard/logs/err.log`. Remove meeting titles, names and anything else private first.
