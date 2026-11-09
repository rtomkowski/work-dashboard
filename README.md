# Work Dashboard

**One local page for the whole working week on macOS.** Calendar, Reminders, Obsidian tasks and notes, and Jira issues side by side, refreshed in the background, with search and commands, time statistics, a weekly review, an 8-week load forecast, time blocking, quick capture and meeting prep built in.

<p align="center"><img src="docs/hero.png" width="540" alt="Work Dashboard: calendar, reminders, Obsidian and Jira in one dashboard on your Mac"></p>

<sub>All screenshots use made-up demo data (`demo/`), not real calendar content. Each one shows the whole page: the window is grown until no panel has anything left to scroll.</sub>

**Full user guide with a screenshot of every feature: [docs/GUIDE.md](docs/GUIDE.md).**

**Interactive map of the page, what each part does: [Work Dashboard anatomy](https://rtomkowski.github.io/work-dashboard/anatomy/).**

## Highlights

- **Everything in one agenda.** Meetings from Exchange and iCloud, Apple Reminders, `- [ ]` tasks from Obsidian and Jira issues in Day, Week, Month and Year views, with overdue items, overlap warnings and booked hours per day. The Week view is a list or a time grid with free gaps and a live now line.
- **Outlook category colours.** Exchange meetings take the colour of their Outlook category, as in Outlook itself; events without one keep their calendar colour.
- **Fits a narrow window.** At 900 px wide or less the greeting shrinks to one line and Now / Next to a slim strip, so the week starts near the top.
- **Full screen.** One click or `F` hides the browser bars; `Esc` brings them back.
- **Event details on a click.** Click a meeting in the Day view and a panel opens beside the timeline and stays: time with a starts-in badge, location, organizer, a Join button for Teams, Zoom or Meet, foldable attendees with RSVP answers, notes with clickable links, and the Jira issues and Obsidian notes the event mentions.
- **Clean course schedules.** Subscribed and course feed calendars (e.g. a university course feed such as `MG2045-HT26`) no longer double up: one entry per session next to your own, and Statistics count the slot once.
- **Now and next.** A greeting with the local weather, the current meeting with time left, what comes next and today's load at a glance.
- **Board.** Backlog, To Do, Doing and Done across all sources; dragging a Jira card changes its status in Jira.
- **Where the time goes.** Statistics group calendar time by title rules (teaching, projects, papers, admin...) and compare with the previous period.
- **What is coming.** Ahead forecasts the next 8 weeks: booked now plus the late additions your calendar usually gets, and the best week for focused work.
- **Act from the page.** Plan time blocks with free-time suggestions (booked through Outlook with a category) and add many reminders at once, each batch with Undo.
- **Notes from the page.** Create an Obsidian note (meeting, idea, literature, blank or your own template) in a chosen folder, or a meeting note straight from an event with its date, place and attendees. Create-only: nothing in the vault is ever overwritten.
- **Weekly review.** What got done, what slipped, where the calendar time went, which notes you touched and how next week looks; saved to Obsidian as a visual note (properties, callouts, Mermaid charts) by itself every Friday afternoon, or by hand.
- **Warns before the week gets tight.** No lunch break, long stretches of back-to-back meetings, overbooked days, and a 30-day deadline radar from Jira, note frontmatter and calendar titles.
- **Find anything, run anything.** ⌘K opens a palette that searches events, tasks, Jira issues and the full text of your notes, and runs every command from the keyboard.
- **Ready for the next meeting.** Earlier notes of the series, notes linked to the meeting and Jira issues for the meeting coming up.
- **From thought to task.** Capture a line into Obsidian with + in the palette, turn a reminder into a Jira issue, time your focus on an issue and log it to Jira, and triage the Obsidian Inbox into folders.
- **Knows when it is broken.** One dot per source shows whether Calendar, Reminders, Obsidian and Jira synced; a failing source keeps its last good data and the panel says why, with a hint to fix it.
- **Private by design.** Runs on 127.0.0.1 on your own Mac, with an access token on every request as a second layer behind Tailscale. Calendar content never leaves it; only Jira API calls and the weather coordinates go out.

## Screenshots

| Day | Event panel |
|---|---|
| ![Day view](docs/screenshots/day.png) | ![Day view with the event detail panel open](docs/screenshots/event-panel.png) |
| **Board** | **Month** |
| ![Board](docs/screenshots/board.png) | ![Month view](docs/screenshots/month.png) |
| **Statistics: History** | **Statistics: Ahead** |
| ![Statistics history](docs/screenshots/stats-history.png) | ![Statistics ahead](docs/screenshots/stats-ahead.png) |
| **Plan blocks** | **New reminders** |
| ![Plan blocks](docs/screenshots/plan-blocks.png) | ![New reminders](docs/screenshots/new-reminders.png) |
| **Week (list)** | **Week (grid)** |
| ![Week view](docs/screenshots/week.png) | ![Week view as a time grid](docs/screenshots/week-grid.png) |
| **Dark theme** | **Year** |
| ![Week view, dark](docs/screenshots/week-dark.png) | ![Year view](docs/screenshots/year.png) |
| **Weekly review** | **Search and commands (⌘K)** |
| ![Weekly review](docs/screenshots/weekly-review.png) | ![Command palette](docs/screenshots/palette.png) |
| **Meeting prep** | **New note** |
| ![Event panel with Prep](docs/screenshots/meeting-prep.png) | ![New note dialog](docs/screenshots/new-note.png) |
| **Inbox triage** | **New Jira issue** |
| ![Inbox triage](docs/screenshots/inbox-triage.png) | ![New Jira issue](docs/screenshots/new-jira-issue.png) |
| **Data sources** | |
| ![Data sources panel](docs/screenshots/data-sources.png) | |

## Install

**Requirements:** a Mac with macOS 13 or later, Python 3.9+ (comes with Apple's Command Line Tools), Apple Calendar and/or Reminders with your accounts added. Optional: Obsidian, Jira Cloud, Microsoft Outlook, Tailscale (for iPhone and iPad).

```bash
git clone https://github.com/rtomkowski/work-dashboard.git
cd work-dashboard
./install.sh
```

The installer asks which optional parts you use (Obsidian, Jira, Outlook, weather, iPhone/iPad access) and sets everything up. Step by step, with troubleshooting: **[INSTALL.md](INSTALL.md)**.

## Try it with dummy data

No Calendar, Reminders, Obsidian or Jira access needed; the demo serves the real `dashboard.html` with generated data (dates relative to today). It writes nothing to disk and sends nothing anywhere: new notes, capture, Inbox triage, new Jira issues, the focus timer, the countdown and Board moves work in memory until the demo stops; Plan blocks, New reminders and settings are refused.

```bash
python3 demo/serve_demo.py      # then open http://127.0.0.1:8766
```

Refresh the screenshots (needs Google Chrome and Node 22 or later, no npm packages; `CHROME=/path/to/chrome` for another Chromium). Each shot starts at 1440 × 900 and grows taller until every panel shows all of its content:

```bash
node demo/screenshots.mjs       # with the demo server running; writes docs/screenshots/
python3 demo/guide_shots.py     # the guide images in docs/guide/ (Python Playwright)
```

## Daily use

Open it at **http://127.0.0.1:8765** (bookmark it), or on the iPad and iPhone through Tailscale (`tailscale serve` to port 8765; Share > Add to Home Screen opens it full screen as "Work").

**Access token.** Every request needs a token (a second layer behind `tailscale serve`), kept in the macOS Keychain (`WorkDashboard-access`). `update.sh` opens the Mac's enrolment link; for the iPad or iPhone run `~/Library/Application\ Support/WorkDashboard/.venv/bin/python ~/Library/Application\ Support/WorkDashboard/auth.py show` and open the link once on the device. The page then keeps a cookie for about a year. `auth.py rotate` makes a new token; every device enrols again.

**Full screen.** The full screen button in the toolbar (left of the right panel toggle) or `F` hides Safari's address bar, tabs and menu bar; `Esc`, `F` or the button bring them back. The browser only allows it from a click or key press, so it does not start by itself on load. Not on iPhone (Safari has no full screen for pages there); use Add to Home Screen instead.

Everything runs on this Mac; nothing leaves it except the Jira API calls and the weather request (only the coordinates from config `weather`, to Open-Meteo).

## What it shows

| Source | How it is read | What appears |
|---|---|---|
| Calendar (Exchange, iCloud, subscribed) | macOS EventKit | Events in all views, booked hours, overlap marks and load warnings; Exchange events coloured by their Outlook category (read-only through Microsoft Outlook); copies from subscribed and course feed calendars collapse to one; the event panel (Day view) with attendees, notes, Join link, linked Jira issues and notes, a Meeting note button and Prep |
| Reminders | macOS EventKit | Open reminders by due date (each opens in Apple Reminders), completed ones of the last 14 days on the Board and in the Weekly review |
| Obsidian | The `.md` files in the vault folders you pick | `- [ ]` tasks with `📅 YYYY-MM-DD` or `due:: YYYY-MM-DD`, note cards (opening in Obsidian), `#dashboard` pins, `due:` / `deadline:` from frontmatter in the deadline radar, notes that mention a Jira key shown on that issue, full-text search |
| Jira (one project, e.g. KAN) | Jira Cloud REST API, token in the macOS Keychain | Issues by Epic, Board columns by status, due dates in the deadline radar, notes that mention the issue |

### Views

Switch views with the icons under the weather in the greeting (counts as badges: events today and this week, Doing, overdue in red), the keys below, or the palette. The toolbar row with the date holds Plan blocks, New note, New reminder and the countdown on the right.

- **Day**: timeline with the event panel; clicking an event opens it beside the timeline (‹ › step through the day, Esc closes).
- **Week**: List or Grid (switch at the top, kept per browser). List: agenda per day; days already passed fold into one row; amber chips for no lunch break or long stretches without a break. Grid: hours down the left (type From / To times or pick them from a half-hour list to set the range, any minute such as 7:30 or 0830, default 07:00 to 19:00), Monday to Friday plus the weekend only when it holds something, overlaps side by side with a warning outline, gaps of an hour or more marked "x h free", a red now line across the week, tasks and notes in a row under the grid.
- **Month**, **Year** (booked hours per day as shading).
- **Board**: Backlog, To Do, Doing, Done across all sources (see Board rules).
- **Overdue**: everything past its due date.
- **Statistics**: History (where the calendar time went, by title rules) and Ahead (the coming 8 weeks).
- **Weekly review**: done, slipped, booked hours, calendar time by group, notes created and edited, and next week; ‹ › step by week; save as a weekly note in Obsidian or copy as Markdown.

### Right panel

Date and time, month calendar, booked hours for the next 14 days, Now / Next, today's numbers, hours this week, Up next, Pinned notes, **Deadlines, next 30 days** (countdown from Jira due dates, note frontmatter `due:` / `deadline:`, and events or reminders whose title says deadline, due, submission, frist, sista dag, last day, inlämning or closes), **Heads up, next 7 days** (overbooked days, overlaps, no lunch break, long stretches) and **Next meeting** (prep for the next meeting today or tomorrow).
Every block has a grip to drag it up or down, a chevron to fold it and ✕ to hide it. Order and hiding are saved in the current profile (Settings > Right panel shows hidden blocks again); folding is remembered per browser.

### Acting from the page

| What | How | What it changes |
|---|---|---|
| Search and commands | ⌘K, `/` or the magnifier | Nothing. Searches commands, events, tasks, Jira issues, notes and the full text of notes |
| Quick capture | In the palette, type `+ text` and Enter | Appends one line: text as `- date time text` to `Inbox/WorkDashboard/Capture from Work Dashboard.md`, a task as `- [ ] text` to `Inbox/WorkDashboard/List from Work Dashboard.md` (defaults) |
| New note | Shift N, the toolbar, or Meeting note in the event panel | Creates a new note in an allowed folder from a template (meeting, idea, literature, blank, or the vault's Templates folder) |
| Weekly review note | Save as weekly note in the Weekly review, or by itself after Friday 16:00 | Creates `YYYY-Www Weekly review` in `review_folder` (default Inbox/WorkDashboard), once per week, never over an existing note |
| WFH day | WFH button on a day in Week or Day (Settings > Workday > Mark WFH days, off by default; office is the default) | Writes the mark to `data/workplace.json` on the Mac only; counted in the Weekly review (`wfh_days`, `wfh_dates`) |
| Inbox triage | `I` or the palette | Moves a note out of the Inbox folder into an allowed folder; Undo last move |
| New reminders | `R`, `N` or the toolbar | Creates reminders, one per row, each batch tagged; Undo removes the last batch |
| Plan blocks | `P` or the toolbar | Creates busy time blocks (through Outlook with a category, else EventKit); Undo removes the last batch |
| New Jira issue | `J`, the palette, or → Jira on a reminder or Obsidian card on the Board | Creates an issue (default Epic from `jira.default_epic`); a promoted reminder or task is not changed |
| Focus timer | ▶ on a Jira card on the Board, or the palette | Stop posts a worklog with the minutes and an optional comment; Discard logs nothing. One timer, shared by all devices |
| Countdown | Stopwatch button in the top bar, or `A` | Minutes (default 20, − / +, quick 10 20 30 45) and a short text, then Start. The button shows the text and time left; click it for +5 min or Stop. When it ends the button turns red, open pages beep for up to a minute and the Mac shows a notification with sound; one click stops it. One countdown at a time, shared by all devices (`data/alarms.json`) |
| Hold / Resume | ⏸ on a reminder row or Board card, ▶ Resume to undo, or drag the card into or out of On hold | Adds `#hold` as the last line of the reminder's notes, or removes every hold mark (`#hold`, `#onhold`, `#on-hold`, `@hold`); nothing else of the reminder changes. Held reminders show only on the Board (On hold) and under Overdue |
| Board moves | Drag a card (or ⇄ on touch) | Jira cards change status in Jira; other cards only move on the dashboard |

### What the dashboard may change

It never edits or deletes anything it did not create, with these exact exceptions:

- **Calendar**: creates Plan blocks; Undo removes only the events of its own last batch (tag in the notes plus stored id).
- **Reminders**: creates reminders; Undo removes only open reminders of its own last batch; Hold / Resume adds or strips the hold mark in the notes of an open reminder in a writable list.
- **Obsidian**: creates new notes (never overwriting: a taken name gets " 2"); appends one line at the end of the capture file or the list file; moves notes only out of the Inbox folder, into allowed folders, never onto an existing file. Allowed folders are `obsidian.write_folders`, or the folders it reads when that is empty, and their subfolders.
- **Jira**: changes status when you drag a card; creates issues; posts worklogs from the focus timer; adds or removes the labels `hold` and `delegated` (Hold/Resume, On hold and Delegated columns).

### Shortcuts

`⌘K` or `/` search and commands · `D` `W` `M` `Y` `B` `O` `S` `V` views · `T` today · `←` `→` previous / next · `R` or `N` new reminder · `Shift N` new note · `J` new Jira issue · `I` Inbox triage · `A` countdown · `P` plan blocks · `,` settings · `1` to `9` profiles · `[` `]` side panels · `F` full screen · `Esc` closes the open dialog, the event panel or a drawer, and leaves full screen.

## Board rules

- Reminders and Obsidian cards: the column is stored locally in `data/kanban.json`; sources are never modified. Done hides a task from the other views.
- Jira cards: the column follows the Jira status (Backlog, To Do, In Progress, Done) and dragging a card changes the status in Jira.
- Automatic placement: overdue or due within 7 days goes to To Do, everything else to Backlog.
- Delegated: Jira issues in the status `Delagated` (or `Delegated`), shown in purple everywhere. Moving a Jira card here sets that status in Jira; moving it out sets Backlog, To Do, In Progress or Done. Only Jira issues can be delegated. The Day view lists them under Delegated, with the overdue count.
- On hold: reminders with `#hold`, `#onhold`, `#on-hold` or `@hold` in their notes, and Jira issues with the label `hold` (or `onhold`, `on-hold`) or `#hold` in the description, whatever column they had. Moving a card here writes the mark (reminder notes) or the `hold` label (Jira); moving it out removes it. A mark in a Jira description is never removed by the dashboard.
- Delegated: moving a Jira card here sets the Delegated status and adds the label `delegated`; moving it out removes the label.
- Reminder and Obsidian cards have → Jira (make an issue from it); Jira cards have ▶ (start the focus timer) and show the two latest notes that mention the issue.

## Settings and profiles

⚙︎ Settings (`,`): a **profile** is what the page looks like when it opens (start view, side panels and widths, theme, right panel blocks and their order, filters, toolbar buttons including New note, view names, phone tab bar, Week and Day details such as load bars, overlap marks and load warnings). Five starters (Overview, Focus today, Planning, Review, Phone glance); profiles live on the Mac (`data/profiles.json`) so every device sees the same set, and each browser remembers which one it opens with. **Workday** (hours of a full day, work from home days on/off) and **Data** (refresh interval, synced window, weather, Jira on/off and JQL, work hours) apply to every profile.

## Files

| File | Role |
|---|---|
| `harvest.py` | Collects all sources into `data.json` (EventKit, Obsidian, Jira) with per-source health; creates Plan blocks events and reminders; Jira create, Epics and worklogs |
| `notes.py` | Obsidian writes: allowed folders, templates, create-only new notes and the weekly review, append-only capture, Inbox triage moves |
| `server.py` | Local web server on 127.0.0.1, refresh timer, all APIs (listed at the top of the file), background harvest after a change, focus timer state |
| `alarms.py` | Countdown state, ring check every second, Mac notification and sound (osascript, afplay) |
| `stats.py` | Statistics: calendar time by title rules |
| `ahead.py` | Ahead: available time and expected load for the coming weeks |
| `planner.py` | Plan blocks: free time, slot suggestions and ranking |
| `outlook.py` | Plan blocks: Outlook categories and booking through Microsoft Outlook (AppleScript) |
| `auth.py` | Access token: Keychain secret, cookie or Bearer check on every request, enrolment links (`show`), `rotate`, `status` |
| `dashboard.html` | The whole UI (single file, no external dependencies) |
| `config.example.json` | Defaults in Git (no personal data); the live copy is in `~/Library/Application Support/WorkDashboard/config.json`. A local `config.json` here is ignored by Git and used by install.sh if present |
| `install.sh` | First-time install: Python venv, dependencies, permissions app, login service |
| `update.sh` | Deploy changes from this folder; adds new config keys, keeps your values |
| `make_app.sh` | Builds `~/Applications/WorkDashboard.app` so macOS can grant Calendar and Reminders access |
| `uninstall.sh` | Stops and removes the login service |
| `demo/` | Dummy data (`demo_data.py`), the demo server (`serve_demo.py`) and the screenshot scripts (`screenshots.mjs` for the README, `guide_shots.py` for the guide) |
| `docs/screenshots/`, `docs/guide/` | README and guide screenshots, made from the demo data, whole page each |

Runtime files in `data/` (never in Git): `data.json` (snapshot), `kanban.json`, `profiles.json`, `plan_batches.json`, `reminder_batches.json`, `timer.json`, `inbox_moves.json`.

## Install and update

```bash
./install.sh      # first time (asks questions)
git pull && ./update.sh    # new version; keeps your settings
./uninstall.sh    # stop it; --purge also deletes settings and data
```

Runtime location: `~/Library/Application Support/WorkDashboard/` (code copy, `config.json`, `data/`, `logs/`, `.venv/`).
Service: LaunchAgent `local.workdashboard`, started at login, restarted if it stops.

## Configuration (live `config.json`)

Edit the live copy; `update.sh` only adds keys that are missing. Many of these are also in Settings > Data.

**General**
- `user_name`: name in the greeting.
- `port` (8765): the local port. `refresh_minutes` (5): how often the sources are read.
- `output_dir`: where the snapshot and the other runtime files go.
- `alarms`: countdown on the Mac: `mac_notify` (true) shows a notification, `mac_sound` ("Glass", any name in /System/Library/Sounds) played `sound_repeats` (3) times, `snooze_minutes` (5).

**calendar**
- `days_back` / `days_ahead` (35 / 70): the synced window.
- `exclude_calendars`: calendar names left out.
- `schedule_feeds` (`*-HT??`, `*-VT??`): course schedule feeds; one entry per session, your own entry at the same time wins. `[]` turns it off.
- `category_colors` (true, v0.70): colour events of the Exchange `planner.outlook_calendar` by their first Outlook category that has a colour. After each harvest the categories are read from Microsoft Outlook (read-only, 15 min cache, about 10 s when not cached). `false` turns it off; so does `planner.outlook` = false.

**reminders**
- `exclude_lists`: lists left out.
- `completed_days` (14): how far back completed reminders are read (Board Done, Weekly review).

**obsidian**
- `vault_path`, `vault_name` (the name in `obsidian://` links; must match the vault name on the iPad and iPhone too).
- `folders`, `files`, `exclude`: what the dashboard reads; easiest in Settings > Data > Obsidian sources.
- `recent_days` (7), `max_notes` (300), `max_undated_tasks` (40): how many notes and undated tasks are kept.
- `pin_tag` (`dashboard`): notes with this tag are pinned in the right panel.
- `review_folder` (Inbox/WorkDashboard): where the weekly review goes. `review_auto` (true): write it by itself; `review_auto_hour` (16): from this hour on Friday.
- `write_folders` (empty = the folders above): where New note, the weekly review, capture and triage may write; subfolders included.
- `note_folder` (`Inbox`), `meeting_folder`, `review_folder` (empty = note_folder): the folder each dialog starts in.
- `templates_folder` (`Templates`): its `.md` files appear as templates; `{{title}}`, `{{date}}` and `{{time}}` are filled in.
- `watch_seconds` (10): how often hand edits in the Obsidian folders are noticed and re-read (0 = only with the full harvest).
- `capture_text_file` (`Inbox/WorkDashboard/Capture from Work Dashboard.md`) and `capture_task_file` (`Inbox/WorkDashboard/List from Work Dashboard.md`): where quick capture appends text and tasks. `capture_file` (`Inbox/Capture.md`) is the old single file, used only when those two are empty. `capture_daily_folder`: when set, both go to `<folder>/YYYY-MM-DD.md` instead.
- `inbox_folder` (`Inbox`): the folder Inbox triage moves notes out of.

**jira**
- `enabled`, `site`, `email`, `keychain_service` (`work-dashboard-jira`). API token: `security add-generic-password -U -s work-dashboard-jira -a <email> -w '<token>'`.
- `jql` (empty = all open issues of the project plus Done in the last 14 days).
- `project` (`KAN`), `issue_type` (`Task`): for New Jira issue. `default_epic` (e.g. `KAN-29`): the Epic every new issue starts with; empty = no Epic.

**stats**, **planner**, **weather**: see Statistics, Ahead and Plan blocks below, and the notes in `config.example.json`. `weather.enabled`, `place`, `lat`, `lon` set the greeting's weather.

## Troubleshooting

- Dots at the bottom of the sidebar (Calendar, Reminders, Obsidian, Jira): click them for the Data sources panel with the error, a hint and Refresh now. A red banner above the view says the same and can be hidden until something changes. A failing source keeps its last good data on the page.
- Calendar or Reminders denied: System Settings > Privacy & Security > Calendars / Reminders > enable WorkDashboard.
- "Could not write the note" or a failed capture or move: the folder must be in `obsidian.write_folders` (or the read folders) and synced by your cloud drive.
- Jira errors when creating an issue or logging work: the Data sources panel shows whether the token works; the message in the dialog comes from Jira.
- Logs: `~/Library/Application Support/WorkDashboard/logs/err.log`.
- Restart: `launchctl kickstart -k gui/$(id -u)/local.workdashboard`.
- Exchange calendar missing: it must be added in System Settings > Internet Accounts; BusyCal's own Exchange account is invisible to EventKit.
- Old system Python 3.8 (Intel-only) is skipped automatically; Apple's `/usr/bin/python3` is used.

## Plan blocks (v0.17)
`P` opens Plan blocks: rows with a title, time, length, notes and an Outlook category; a day or week calendar on the right shows your events and free bands, and clicking it places the selected block. Free times are suggested by fit, buffer (`planner.buffer_minutes`), load and habits. Blocks for the Exchange `planner.outlook_calendar` are booked through Microsoft Outlook (`planner.outlook`, `outlook_account`) so the category from `planner.outlook_categories` is set; other calendars go through EventKit (`planner.calendar` is the default).

## Statistics (v0.11)
The Stats view groups calendar time by the title rules in `config.json` -> `stats`:
- `groups`: ordered rules (first match wins, case-insensitive regex on the event title). `Absence` marks time off, `Ignore` is not counted, anything unmatched is `Uncategorized`.
- `supergroups`: which categories belong to which group.
- `out_of_office_as`: work events Exchange marks out of office count as this category (off-site days), except the listed ones.
- `category_map` (v0.67): Outlook category -> category, e.g. `{"Focus Work": "Focus time", "Edu": "Teaching"}`. The dashboard reads the categories of the events in the Exchange `planner.outlook_calendar` from Microsoft Outlook (read-only, cached 15 min; a recurring series by its title) and uses them before the title rules; a `[Tag]` at the end of a title still wins. An empty target or an unmapped category leaves the event to the title rules. With no entries Outlook is not asked. If Outlook cannot be read, Statistics says so and counts by title rules only. Applies to Statistics, Ahead and the weekly review.
- `vacation_weeks`: ISO weeks left out of the weekly figures; also editable by clicking a week in the chart.
- `work_hours` (default 8-18), optional `nominal_hours_per_day` (default 8) and `exclude_calendars`.
After deployment the live copy is `~/Library/Application Support/WorkDashboard/config.json`; edit rules there (update.sh only adds missing keys).

## Ahead (v0.12)
Statistics -> Ahead looks 8 weeks forward with the same rules. Expected load = booked now + the typical late additions for that lead time, learned from the creation dates of the last 26 weeks of events (`ahead.py`). If fewer than 60% of past events have a usable creation date it uses recent comparable weeks instead.


## Contributing and licence

Ideas, bug reports and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). MIT licence, see [LICENSE](LICENSE).
