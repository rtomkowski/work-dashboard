"""Local watchdog: re-harvests every N minutes and serves the dashboard on 127.0.0.1.

GET  /             dashboard page
GET  /data.json    latest snapshot
GET  /api/stamp    snapshot change stamp (the page polls it every 10 s; v0.44)
POST /refresh      harvest now, return the fresh snapshot
POST /api/calendar/show {"start"} open Calendar.app on that day (event panel)
GET  /api/vault    Obsidian folder tree + current selection (settings panel)
POST /api/obsidian save folders/files/exclude to config.json, harvest, return snapshot
POST /api/kanban   {"id": task id, "col": backlog|todo|doing|done|null} board column overlay
POST /api/reminders {"title", "list", "date", "time", "priority", "notes"} create a reminder (create-only);
                    {"titles": [...], plus the same shared fields} creates one reminder per title (max 50);
                    {"items": [{"unit", "title", "list", "date", "time", "priority", "notes"}]} the New reminder
                    dialog (v0.27): each row its own fields, tagged so the last batch can be undone
POST /api/reminders/undo {"batch"}   remove the reminders of the last batch (tagged, still open ones only)
GET  /api/reminders/last      the last reminder batch, for the Undo button
POST /api/jira/hold {"key", "hold": true|false}   add the `hold` label, or remove hold/onhold/on-hold labels (v0.50)
POST /api/reminders/hold {"ids": ["rem:..."], "hold": true|false}   put reminders on hold or resume them: adds #hold to
                    the notes or strips #hold/#onhold/@hold (v0.47, KAN-88); nothing else of the reminder changes
GET  /api/reminders/days?until=YYYY-MM-DD   events per day for the New reminder calendar (nothing is created)
GET  /api/stats?period=this_year|last_week|last_month|last_12m|custom&from=&to=   calendar time statistics (aggregates, plus titles of ungrouped events)
POST /api/stats/vacation {"week": "2026-W15", "on": true}   mark or unmark a vacation week (config.json)
GET  /api/ahead?weeks=8   available time and expected load for the coming weeks (aggregates only)
POST /api/plan/suggest {"rows": [...]}   clash check for chosen times and free times per row (nothing is booked)
POST /api/plan/book {"calendar_id", "blocks": [{"unit", "title", "notes", "category", "start", "end", "obsnote"?}], "force"}   create the blocks
                     (obsnote: a note template id; v0.55 creates that Obsidian note for the booked block, linked to it)
                      (through Outlook with the category for the Outlook calendar, else through EventKit)
POST /api/plan/undo {"batch"}   remove the events of the last booked batch (tagged ones only)
GET  /api/plan/last           the last booked batch, for the Undo button
GET  /api/weather             current weather and today's range for the greeting (Open-Meteo, cached 30 min)
GET  /api/profiles            visual profiles and dashboard-wide settings (data/profiles.json)
POST /api/profiles {"profiles": [...], "global": {...}}   save them
GET  /api/settings            the server settings the Settings panel edits (part of config.json)
POST /api/settings {...}      save them to config.json, harvest, return {"settings", "data"}
GET  /manifest.webmanifest, /apple-touch-icon.png   Add to Home Screen on iPhone / iPad
GET  /api/notes/targets       folders the dashboard may write notes to, templates, default folders (v0.37)
GET  /api/search?q=   words in the text of the Obsidian notes the dashboard reads (v0.41, KAN-77), with a snippet
GET  /api/jira/epics          open Epics of the Jira project, for New Jira issue (v0.42, KAN-71)
POST /api/jira/issue {"summary", "epic", "priority", "due", "description"}   create a Jira issue (create-only)
GET  /api/timer               the running focus timer, shared by all devices (data/timer.json; v0.42, KAN-82)
POST /api/timer {"action": "start", "key", "title"} | {"action": "stop", "minutes", "comment"} | {"action": "discard"}
                    stop posts a worklog to that Jira issue
POST /api/capture {"text", "task"}   append one line: text to the capture file, task to the list file (append-only; v0.42, KAN-72; v0.44)
GET  /api/inbox               notes in the Inbox folder, oldest first, and where they can go (v0.42, KAN-78)
POST /api/inbox/move {"path", "folder"}   move one Inbox note into a whitelisted folder (never overwrites)
POST /api/inbox/undo          move the last moved note back
GET  /api/alarms              alarms (countdowns with a description), server clock, done today (v0.43, KAN-84)
POST /api/alarms {"action": "add", "label", "minutes" | "at": "HH:MM", "kind"} | {"action": "pause" | "resume" |
                    "cancel" | "dismiss" | "again", "id"} | {"action": "snooze" | "plus", "id", "minutes"} |
                    {"action": "clear_rung"}   (data/alarms.json; a ringing alarm also raises a Mac notification)
POST /api/notes {"folder", "title", "template", "event_id"?, "content"?}   create a new Obsidian note (create-only,
                    whitelisted folders, never overwrites; "content" is the weekly review export)
POST /api/review/auto {"week": "YYYY-MM-DD" (Monday), "title", "content"}   automatic weekly review (v0.45, KAN-86):
                    create-only in review_folder, once per ISO week, only after Friday review_auto_hour, at most
                    3 weeks back (data/review_auto.json)
POST /api/wfh {"date": "YYYY-MM-DD", "state": "home" | "office" | null}   mark a work from home day (v0.46, KAN-87;
                    data/workplace.json; "office" overrides a day a WFH calendar event would mark; null clears)
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import alarms
import harvest
import notes
import stats
import ahead
import planner
import outlook
from datetime import date, datetime, timedelta
import auth  # KAN-42 access token

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()
VERSION = "0.71"
LABEL = "local.workdashboard"
STARTED = time.strftime("%Y-%m-%dT%H:%M")
HEAL_EVERY = 30 * 60   # seconds between automatic restarts

# 180x180 home-screen icon (iOS rounds the corners itself)
ICON_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0AgMAAABAo+6hAAAACVBMVEX////JyfNbW9ZuduftAAABO0lEQVR42u2XSwrCMBCG09JCcO0BXIqn8AhuFI+To7guHqKnEJfd61oKSuui9JVmpikNCumfTSb0YyaPZuZPkIgJLRSgQYMGDRr0EumoNU9CCCF2ysr3q+rudjPJqq6wo5XWs3ShxXC5J3nXKM49KOF95/2h4umMGJppRYTy64/VVil5ekMMzbQkQkXm+SXe3vlOK6+1FR/GfT8b6+183qnR/McO7o2mG9/rxorNpyO7RnB0usqQuDcuMnLtM7Tbk1XVbTk6wKsONGhv6s6NTiQJnX04kTDUg5zMCgmZ5qmukgwk6drAyaxpp7OM/N3+349U/9bXVD3f5QDWNNWMeX94efXDHYx4eTXLdzBwpGkqaAjUHaburC6s6uWe9qN1pzDS+WjK9LruKPZNwgUfPR2JOw8aNGjQoN3RX7jnNcjXz0MLAAAAAElFTkSuQmCC")
MANIFEST = {"name": "Work Overview", "short_name": "Work", "start_url": "./", "display": "standalone",
            "background_color": "#f4f4f6", "theme_color": "#5b5bd6",
            "icons": [{"src": "apple-touch-icon.png", "sizes": "180x180", "type": "image/png"}]}


SHOW_DAY = '''on run argv
	set d to current date
	set day of d to 1
	set year of d to (item 1 of argv) as integer
	set month of d to (item 2 of argv) as integer
	set day of d to (item 3 of argv) as integer
	set time of d to (item 4 of argv) as integer
	tell application "Calendar"
		activate
		switch view to day view
		view calendar at d
	end tell
end run
'''


def show_in_calendar(start: str) -> dict:
    """Event panel (v0.36, KAN-67): bring Calendar.app to the event's day. Read-only; date passed as arguments."""
    dt = datetime.strptime(start[:16], "%Y-%m-%dT%H:%M")
    r = subprocess.run(["osascript", "-", str(dt.year), str(dt.month), str(dt.day), str(dt.hour * 3600 + dt.minute * 60)],
                       input=SHOW_DAY, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        err = r.stderr.strip()
        return {"error": "WorkDashboard may not control Calendar (System Settings > Privacy & Security > Automation)"
                if "-1743" in err else (err[-200:] or "osascript failed")}
    return {"ok": True}


def run_harvest() -> dict:
    with LOCK:  # timer, button and settings never harvest at the same time
        data = harvest.harvest(outlook_colors)
    self_heal(data)
    return data


def outlook_colors(data: dict, cfg: dict):
    """v0.70 (KAN-110): Outlook categories and their colours on the snapshot's events, so the page colours an
    event like Outlook does. Read-only; only events of the planner.outlook_calendar on Exchange are matched
    (title and start, recurring by series title). Off with calendar.category_colors = false or planner.outlook."""
    cal, p = data["calendar"], cfg.get("planner") or {}
    if not (cfg.get("calendar") or {}).get("category_colors", True) or not p.get("outlook", True):
        return
    name = p.get("outlook_calendar", "Calendar")
    ex = {c.get("name") for c in cal.get("calendars") or [] if c.get("exchange")}
    own = [e for e in cal.get("events") or [] if e.get("calendar") == name and name in ex]
    if not own:
        return
    w = data.get("window") or {}
    a = date.fromisoformat(w["from"][:10])
    b = date.fromisoformat(w["to"][:10]) + timedelta(days=1)
    try:
        idx = outlook.event_categories(a, b, p.get("outlook_account", ""), name)
        cal["categories"] = outlook.categories()
        cal["categories_tagged"] = outlook.tag_events(own, idx)
    except Exception as e:
        harvest.log(f"colours: outlook categories failed: {type(e).__name__}: {e}")
        cal["categories_error"] = f"{e}"[:300]


def self_heal(data: dict):
    """Safety net for KAN-41: if Calendar/Reminders still say 'denied', restart the service the way
    `launchctl kickstart -k` does by hand. At most once per HEAL_EVERY, so a permission that is
    really switched off cannot cause a restart loop."""
    errs = " ".join((data.get("errors") or {}).get(k, "") for k in ("calendar", "reminders"))
    if "denied" not in errs.lower():
        return
    stamp = Path(harvest.load_config()["output_dir"]).expanduser() / "last_selfheal"
    last = stamp.stat().st_mtime if stamp.exists() else 0
    if time.time() - last < HEAL_EVERY:
        harvest.log("access still denied; no restart (last one < 30 min ago)")
        return
    stamp.touch()
    harvest.log("access denied; restarting service (launchctl kickstart -k)")
    # own session, so launchd killing the service does not kill this command
    subprocess.Popen(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


NLOCK = threading.Lock()   # two clicks never race for the same file name


def notes_targets() -> dict:
    cfg = harvest.load_config()
    try:
        ts = notes.targets(cfg)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"folders": ts, "templates": notes.template_list(cfg),
            "default": {k: notes.default_folder(cfg, k) for k in ("note", "meeting", "review")},
            "vault": cfg["obsidian"].get("vault_name") or notes.vault(cfg).name}


def find_event(eid: str):
    try:
        evs = (json.loads(data_path().read_text(encoding="utf-8")).get("calendar") or {}).get("events") or []
    except (OSError, ValueError):
        evs = []
    return next((e for e in evs if e.get("id") == eid), None)


def note_create(body: dict) -> dict:
    cfg = harvest.load_config()
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("Give the note a title")
    content = body.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("Bad content")
    ev = None
    if body.get("event_id"):
        ev = find_event(str(body["event_id"]))
        if ev is None:
            raise ValueError("That event is no longer in the snapshot; refresh and try again")
    with NLOCK:
        out = notes.create(cfg, str(body.get("folder") or ""), title, str(body.get("template") or "blank"),
                           content=content, event=ev)
    harvest.log(f"note created: {out['path']}")
    harvest_soon()   # the new note shows up after the next harvest
    return out


def review_auto_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "review_auto.json"


def review_auto(body: dict) -> dict:
    """Automatic weekly review (v0.45, KAN-86). The page builds the Markdown; the server decides whether it may be
    written: review_auto on, the week's Friday review_auto_hour has passed, not older than 3 weeks, not written before
    and no note of that name in the review folder. Create-only, like New note."""
    cfg = harvest.load_config()
    o = cfg["obsidian"]
    if not o.get("review_auto", True) or not o.get("enabled", True):
        return {"skipped": "off"}
    try:
        mon = date.fromisoformat(str(body.get("week") or ""))
    except ValueError:
        raise ValueError("Bad week")
    if mon.weekday() != 0:
        raise ValueError("week must be a Monday")
    now = datetime.now()
    due = datetime.combine(mon + timedelta(days=4), datetime.min.time()) + timedelta(hours=float(o.get("review_auto_hour", 16)))
    if now < due:
        return {"skipped": "not yet"}
    if (now.date() - mon).days > 21:
        return {"skipped": "too old"}
    title, content = str(body.get("title") or "").strip(), body.get("content")
    if not title or not isinstance(content, str) or not content.strip():
        raise ValueError("Missing title or content")
    iy, iw, _ = mon.isocalendar()
    key, p = f"{iy}-W{iw:02d}", review_auto_path()
    with NLOCK:
        try:
            done = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            done = {}
        if key in done:
            return {"skipped": "already written", "path": done[key]}
        folder = notes.default_folder(cfg, "review")
        have = notes.existing(cfg, folder, title)
        if have:
            out = {"skipped": "exists", "path": have}
        else:
            out = dict(notes.create(cfg, folder, title, content=content), created=True)
        done[key] = out["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, indent=1), encoding="utf-8")
        tmp.replace(p)
    if out.get("created"):
        harvest.log(f"weekly review written: {out['path']}")
        harvest_soon()
    return out


BG_PENDING = threading.Event()


def harvest_soon():
    """One background harvest for a burst of writes; a write during a running harvest queues one more."""
    if BG_PENDING.is_set():
        return
    BG_PENDING.set()

    def go():
        time.sleep(1)
        BG_PENDING.clear()
        try:
            run_harvest()
        except Exception as e:
            harvest.log(f"background harvest failed: {type(e).__name__}: {e}")
    threading.Thread(target=go, daemon=True).start()


SCACHE = {}                  # path -> (mtime, lower-case text, text); note bodies for search, read once per change
SLOCK = threading.Lock()


def _fold(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def search_notes(q: str, limit: int = 40) -> dict:
    words = [w for w in _fold(q).split() if w]
    if not words or len(" ".join(words)) < 2:
        return {"notes": []}
    cfg = harvest.load_config()
    if not harvest.obsidian_on(cfg):
        return {"notes": []}
    vault = Path(cfg["obsidian"]["vault_path"]).expanduser()
    out, seen = [], set()
    with SLOCK:
        for p in harvest.obsidian_scope(cfg["obsidian"], vault):
            seen.add(p)
            try:
                mt = p.stat().st_mtime
                c = SCACHE.get(p)
                if not c or c[0] != mt:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    c = SCACHE[p] = (mt, _fold(txt), txt)
            except OSError:
                continue    # OneDrive placeholder not downloaded, or gone
            low = c[1]
            if not all(w in low for w in words):
                continue
            rel = p.relative_to(vault).as_posix()
            i = low.find(words[0])
            # snippet: the line around the first hit (_fold keeps lengths for most text; clamp anyway)
            line_s = c[2].rfind("\n", 0, i) + 1
            line_e = c[2].find("\n", i)
            line = c[2][line_s: line_e if line_e >= 0 else None].strip()
            if len(line) > 160:
                k = max(0, i - line_s - 60)
                line = ("…" if k else "") + line[k:k + 160] + "…"
            score = (3 if all(w in _fold(p.stem) for w in words) else 0) + (1 if words[0] in _fold(p.stem) else 0)
            out.append({"title": p.stem, "folder": str(Path(rel).parent), "uri": notes.uri(cfg, rel), "snippet": line,
                        "modified": mt, "score": score})
        for k in [k for k in SCACHE if k not in seen]:
            del SCACHE[k]
    out.sort(key=lambda n: (-n["score"], -n["modified"]))
    return {"notes": [{k: v for k, v in n.items() if k not in ("score", "modified")} for n in out[:limit]],
            "more": max(0, len(out) - limit)}


# ---------------------------------------------------------------- Jira create, focus timer (v0.42)
TLOCK = threading.Lock()
PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")


def jira_issue(body: dict) -> dict:
    cfg = harvest.load_config()
    if not cfg.get("jira", {}).get("enabled"):
        raise ValueError("Jira is off (jira.enabled in config.json)")
    summary = re.sub(r"\s+", " ", str(body.get("summary") or "")).strip()[:250]
    if not summary:
        raise ValueError("Give the issue a summary")
    epic = str(body.get("epic") or "")
    if epic and not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", epic):
        raise ValueError("Bad Epic key")
    due = str(body.get("due") or "")
    if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise ValueError("Bad due date")
    prio = str(body.get("priority") or "")
    item = {"summary": summary, "epic": epic, "due": due, "priority": prio if prio in PRIORITIES else "",
            "description": str(body.get("description") or "")[:8000]}
    out = harvest.jira_create(cfg, item)
    harvest.log(f"jira issue created: {out['key']}")
    harvest_soon()
    return out


def timer_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "timer.json"


def timer_get():
    try:
        return json.loads(timer_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def timer_post(body: dict) -> dict:
    act = body.get("action")
    with TLOCK:
        cur = timer_get()
        if act == "start":
            key = str(body.get("key") or "")
            if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key):
                raise ValueError("Bad issue key")
            if cur:
                raise ValueError(f"A timer is already running on {cur['key']}; stop it first")
            t = {"key": key, "title": str(body.get("title") or "")[:200], "started": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            timer_path().write_text(json.dumps(t), encoding="utf-8")
            return {"timer": t}
        if not cur:
            raise ValueError("No timer is running")
        if act == "discard":
            timer_path().unlink(missing_ok=True)
            return {"timer": None}
        if act == "stop":
            try:
                minutes = int(body.get("minutes"))
            except (TypeError, ValueError):
                raise ValueError("Give the minutes to log")
            if not 1 <= minutes <= 24 * 60:
                raise ValueError("Log between 1 minute and 24 hours")
            started = datetime.strptime(cur["started"], "%Y-%m-%dT%H:%M:%S")
            log = harvest.jira_worklog(harvest.load_config(), cur["key"], started, minutes * 60, str(body.get("comment") or "")[:2000])
            timer_path().unlink(missing_ok=True)
            harvest.log(f"worklog {minutes} min on {cur['key']}")
            return {"timer": None, "worklog": log}
    raise ValueError("Unknown timer action")


# ---------------------------------------------------------------- alarms (v0.43, KAN-84)
ALARMS = alarms.Alarms(Path(harvest.load_config()["output_dir"]))


def alarm_loop():
    """Every second: alarms whose time has come start ringing; each raises a Mac notification once."""
    while True:
        try:
            for a in ALARMS.tick():
                harvest.log(f"alarm rang: {a['label']} ({a['minutes']:g} min)")
                alarms.notify_mac(a, harvest.load_config(), harvest.log)
        except Exception as e:  # noqa: BLE001  keep the loop alive
            harvest.log(f"alarm loop: {type(e).__name__}: {e}")
        time.sleep(1)


# ---------------------------------------------------------------- capture and Inbox triage (v0.42)
ILOCK = threading.Lock()


def moves_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "inbox_moves.json"


def inbox_move(body: dict) -> dict:
    cfg = harvest.load_config()
    with ILOCK, NLOCK:
        m = notes.move_note(cfg, str(body.get("path") or ""), str(body.get("folder") or ""))
        try:
            hist = json.loads(moves_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hist = []
        hist = (hist + [dict(m, at=datetime.now().strftime("%Y-%m-%dT%H:%M"))])[-20:]
        moves_path().write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    harvest.log(f"inbox note moved: {m['from']} -> {m['to']}")
    harvest_soon()
    return m


def inbox_undo() -> dict:
    cfg = harvest.load_config()
    with ILOCK, NLOCK:
        try:
            hist = json.loads(moves_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hist = []
        if not hist:
            raise ValueError("Nothing to undo")
        out = notes.unmove_note(cfg, hist[-1])
        moves_path().write_text(json.dumps(hist[:-1], ensure_ascii=False), encoding="utf-8")
    harvest.log(f"inbox move undone: {out['from']} -> {out['to']}")
    harvest_soon()
    return out


def data_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "data.json"


def kanban_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "kanban.json"


KLOCK = threading.Lock()


def load_kanban() -> dict:
    try:
        return json.loads(kanban_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def set_kanban(task_id: str, col):
    """Board columns live only here; Reminders and Obsidian are never modified."""
    with KLOCK:
        k = load_kanban()
        if col in (None, "", "auto"):
            k.pop(task_id, None)
        else:
            k[task_id] = {"col": col, "t": time.strftime("%Y-%m-%dT%H:%M")}
        tmp = kanban_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(k, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(kanban_path())
        return k


# ---------------------------------------------------------------- work from home days (v0.46, KAN-87)

def workplace_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "workplace.json"


def load_workplace() -> dict:
    try:
        d = json.loads(workplace_path().read_text(encoding="utf-8")).get("days") or {}
        return {k: v for k, v in d.items() if v in ("home", "office")}
    except (FileNotFoundError, ValueError, AttributeError):
        return {}


def set_workplace(body: dict) -> dict:
    """Only data/workplace.json is written; the calendar is never touched."""
    day, state = str(body.get("date", "")), body.get("state")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    if state not in ("home", "office", None, ""):
        raise ValueError("state must be home, office or null")
    with KLOCK:
        d = load_workplace()
        if state:
            d[day] = state
        else:
            d.pop(day, None)
        tmp = workplace_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({"days": dict(sorted(d.items()))}, indent=1), encoding="utf-8")
        tmp.replace(workplace_path())
    return {"wfh": d}


def with_kanban(data: dict) -> bytes:
    data["kanban"] = load_kanban()
    data["wfh"] = load_workplace()
    data["service_started"] = STARTED
    data["version"] = VERSION
    return json.dumps(data, ensure_ascii=False).encode()


def save_obsidian_selection(sel: dict):
    clean = lambda xs: [str(x).strip().strip("/") for x in xs if str(x).strip()]
    with LOCK:
        cfg = harvest.load_config()
        cfg["obsidian"]["folders"] = clean(sel.get("folders", []))
        cfg["obsidian"]["files"] = clean(sel.get("files", []))
        cfg["obsidian"]["exclude"] = [str(x).strip() for x in sel.get("exclude", []) if str(x).strip()]
        tmp = HERE / "config.json.tmp"
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(HERE / "config.json")


def clean_reminder(body: dict) -> dict:
    """Validate the quick-add form. Raises ValueError with a message the page can show."""
    title = str(body.get("title", "")).strip()
    if not title or len(title) > 300:
        raise ValueError("Title is required (max 300 characters)")
    date, time_ = str(body.get("date") or ""), str(body.get("time") or "")
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Date must be YYYY-MM-DD")
    if time_ and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_):
        raise ValueError("Time must be HH:MM")
    if time_ and not date:
        raise ValueError("A time needs a date")
    if date:
        time.strptime(date, "%Y-%m-%d")  # rejects 2026-02-30
    prio = int(body.get("priority") or 0)
    if prio not in (0, 1, 5, 9):
        raise ValueError("Priority must be 0, 1, 5 or 9")
    return {"title": title, "list": str(body.get("list") or "")[:200], "date": date, "time": time_,
            "priority": prio, "notes": str(body.get("notes") or "")[:4000]}


MAX_BATCH = 50


def clean_reminders(body: dict) -> list:
    """Batch quick-add: one reminder per title, all sharing list, date, time, priority and notes."""
    titles = [str(t).strip() for t in body.get("titles") or [] if str(t).strip()]
    if not titles:
        raise ValueError("Add at least one title")
    if len(titles) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} reminders at a time")
    return [clean_reminder({**body, "title": t}) for t in titles]


def clean_reminder_items(body: dict) -> list:
    """New reminder dialog: one row per reminder, each with its own list, date, time, priority and notes."""
    rows = body.get("items")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Add at least one reminder")
    if len(rows) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} reminders at a time")
    out = []
    for n, r in enumerate(rows, 1):
        try:
            item = clean_reminder(r if isinstance(r, dict) else {})
        except ValueError as e:
            raise ValueError(f"Reminder {n}: {e}")
        out.append(dict(item, unit=str(r.get("unit") or n)[:40]))
    return out


RLOCK = threading.Lock()     # two clicks never add or undo a reminder batch in parallel


def rem_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "reminder_batches.json"


def rem_last():
    try:
        return json.loads(rem_path().read_text(encoding="utf-8")).get("last")
    except (FileNotFoundError, ValueError):
        return None


def rem_last_summary():
    b = rem_last()
    return {"batch": b["batch"], "at": b["at"], "count": len(b["created"])} if b else None


def rem_add(body: dict) -> dict:
    """Create the rows in one child process, tag each one, keep the batch for undo, re-sync once."""
    items = clean_reminder_items(body)
    with RLOCK:
        batch = time.strftime("%Y%m%d-%H%M%S")
        tag = f"{harvest.REM_TAG}, batch {batch}"
        res = harvest.add_reminders_child([dict(i, tag=tag) for i in items])["results"]
        for r, i in zip(res, items):
            r["unit"] = i["unit"]
        ok = [r for r in res if r.get("ok")]
        if ok:
            tmp = rem_path().with_suffix(".tmp")
            tmp.write_text(json.dumps({"last": {"batch": batch, "at": time.strftime("%Y-%m-%dT%H:%M"), "created":
                           [{"id": r["id"], "title": r["title"], "list": r["list"]} for r in ok]}},
                           ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(rem_path())
    harvest.log(f"reminders batch {batch}: {len(ok)} of {len(res)} created")
    data = json.loads(with_kanban(run_harvest()))   # one re-sync for the whole batch
    data["batch"], data["rem_batch"], data["rem_last"] = res, batch if ok else None, rem_last_summary()
    return data


def rem_hold(body: dict) -> dict:
    """Hold / Resume (v0.47): only the hold marker in the notes changes, then one re-sync."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 50 or not all(isinstance(i, str) and i.startswith("rem:") for i in ids):
        raise ValueError("ids must be 1 to 50 reminder ids")
    if not isinstance(body.get("hold"), bool):
        raise ValueError("hold must be true or false")
    with RLOCK:
        res = harvest.set_hold_child(ids, body["hold"])
    harvest.log(f"reminders: {'hold' if body['hold'] else 'resume'} {len(res['changed'])} changed, {len(res['skipped'])} skipped")
    data = json.loads(with_kanban(run_harvest()))
    data["held"] = res
    return data


def rem_undo(body: dict) -> dict:
    with RLOCK:
        b = rem_last()
        if not b or str(body.get("batch")) != b["batch"]:
            raise ValueError("Only the last reminder batch can be undone")
        res = harvest.remove_reminders_child([c["id"] for c in b["created"]])
        rem_path().unlink(missing_ok=True)
    harvest.log(f"reminders: undo batch {b['batch']}: {len(res['removed'])} removed, {len(res['skipped'])} skipped")
    data = json.loads(with_kanban(run_harvest()))
    data["undone"], data["rem_last"] = res, None
    return data


def rem_days(q: dict) -> dict:
    """Events and free time per day for the New reminder calendar. Same source as Plan blocks, no Outlook call."""
    now = datetime.now()
    until = (q.get("until") or [""])[0]
    last = now.date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
        last = max(last, min(date.fromisoformat(until), now.date() + timedelta(weeks=planner.MAX_WEEKS)))
    events, wl = plan_events(last)
    st = harvest.load_config().get("stats") or {}
    out = planner.Planner(events, st, now, last, wl, plan_cfg()["buffer"]).plan([])
    out.pop("rows", None)
    out["last"] = rem_last_summary()
    return out


def outlook_tag(events: list, cfg: dict, start: date, end: date) -> dict:
    """v0.67 (KAN-105): put Outlook categories on the events ("ocat") so stats.category_map can count them.
    Only when the map has entries and Outlook is on; a failure leaves the title rules alone in charge."""
    st, p = cfg.get("stats") or {}, cfg.get("planner") or {}
    if not any(not str(k).startswith("_") for k in (st.get("category_map") or {})):
        return {"used": False}
    if not p.get("outlook", True):
        return {"used": False, "error": "Outlook is switched off (config planner.outlook)"}
    try:
        idx = outlook.event_categories(start, end + timedelta(days=1), p.get("outlook_account", ""),
                                       p.get("outlook_calendar", "Calendar"))
        return {"used": True, "tagged": outlook.tag_events(events, idx)}
    except Exception as e:
        harvest.log(f"stats: outlook categories failed: {type(e).__name__}: {e}")
        return {"used": False, "error": f"{e}"[:300]}


def stats_payload(q: dict) -> dict:
    """Statistics for the chosen period and the one before it. Raw events never leave this function."""
    cfg = harvest.load_config()
    st = cfg.get("stats") or {}
    kind = (q.get("period") or [st.get("default_period", "this_year")])[0]
    if kind == "calendar_year":
        kind = "this_year"
    s, e, ps, pe, label = stats.period_range(kind, date.today(), (q.get("from") or [""])[0], (q.get("to") or [""])[0])
    events = harvest.period_events_child(ps.isoformat(), e.isoformat())
    oc = outlook_tag(events, cfg, ps, e)
    cur = stats.compute(events, st, s, e)
    prev = stats.compute(events, st, ps, pe)
    keep = ("period", "total_hours", "weekly", "utilisation", "focus_blocks_per_week", "groups")
    return {"label": label, "kind": kind, "current": cur, "previous": {k: prev[k] for k in keep},
            "summary": stats.summarise(cur, prev, label), "vacation_weeks": st.get("vacation_weeks", []),
            "outlook": oc, "generated": time.strftime("%Y-%m-%dT%H:%M")}


def ahead_payload(q: dict) -> dict:
    """Coming weeks plus the history the forecast learns from (last year's same weeks included)."""
    weeks = max(1, min(int((q.get("weeks") or ["8"])[0]), 16))
    cfg = harvest.load_config()
    now = datetime.now()
    monday = now.date() - timedelta(days=now.weekday())
    a, b = monday - timedelta(weeks=53), monday + timedelta(weeks=weeks, days=-1)
    events = harvest.period_events_child(a.isoformat(), b.isoformat())
    outlook_tag(events, cfg, a, b)
    return ahead.forecast(events, cfg.get("stats") or {}, now, weeks=weeks)


def set_vacation(week: str, on: bool) -> list:
    if not re.fullmatch(r"\d{4}-W\d{2}", week):
        raise ValueError("week must look like 2026-W15")
    with LOCK:
        cfg = harvest.load_config()
        st = cfg.setdefault("stats", {})
        weeks = set(st.get("vacation_weeks", []))
        (weeks.add if on else weeks.discard)(week)
        st["vacation_weeks"] = sorted(weeks)
        tmp = HERE / "config.json.tmp"
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(HERE / "config.json")
        return st["vacation_weeks"]



# ---------------------------------------------------------------- Settings and profiles (v0.25)

MAX_PROFILES = 12


def profiles_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "profiles.json"


def load_profiles() -> dict:
    """Profiles are page layout only; null until the first save, then the page shows its starter set."""
    try:
        d = json.loads(profiles_path().read_text(encoding="utf-8"))
        return {"profiles": d.get("profiles"), "global": d.get("global") or {}}
    except (FileNotFoundError, ValueError):
        return {"profiles": None, "global": {}}


def save_profiles(body: dict) -> dict:
    profs, glob = body.get("profiles"), body.get("global") or {}
    if not isinstance(profs, list) or not 1 <= len(profs) <= MAX_PROFILES:
        raise ValueError(f"Keep between 1 and {MAX_PROFILES} profiles")
    ids = set()
    for pr in profs:
        if not isinstance(pr, dict) or not str(pr.get("id", "")).strip() or not str(pr.get("name", "")).strip():
            raise ValueError("Every profile needs an id and a name")
        if pr["id"] in ids:
            raise ValueError(f"Duplicate profile id {pr['id']}")
        ids.add(pr["id"])
        pr["name"] = str(pr["name"]).strip()[:40]
    if not isinstance(glob, dict):
        raise ValueError("global must be an object")
    dh = float(glob.get("day_hours", 8))
    if not 1 <= dh <= 16:
        raise ValueError("Hours in a full day must be between 1 and 16")
    glob["day_hours"] = dh
    out = {"profiles": profs, "global": glob}
    raw = json.dumps(out, ensure_ascii=False, indent=1)
    if len(raw) > 200_000:
        raise ValueError("Profiles too large")
    with LOCK:
        tmp = profiles_path().with_suffix(".tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(profiles_path())
    return out


def settings_payload() -> dict:
    cfg = harvest.load_config()
    cal, w, j, st = cfg.get("calendar") or {}, cfg.get("weather") or {}, cfg.get("jira") or {}, cfg.get("stats") or {}
    wh = st.get("work_hours") or [8, 18]
    return {"refresh_minutes": cfg.get("refresh_minutes", 5),
            "calendar": {"days_back": cal.get("days_back", 35), "days_ahead": cal.get("days_ahead", 70)},
            "weather": {"enabled": w.get("enabled", True), "place": w.get("place", ""),
                        "lat": w.get("lat", 59.35), "lon": w.get("lon", 18.07)},
            "jira": {"enabled": j.get("enabled", False), "jql": j.get("jql", ""), "configured": bool(j.get("email"))},
            "work_hours": [int(wh[0]), int(wh[1])]}


def save_settings(body: dict) -> dict:
    def num(v, lo, hi, what, kind=int):
        try:
            x = kind(v)
        except (TypeError, ValueError):
            raise ValueError(f"{what} must be a number")
        if not lo <= x <= hi:
            raise ValueError(f"{what} must be between {lo} and {hi}")
        return x
    cal, w, j = body.get("calendar") or {}, body.get("weather") or {}, body.get("jira") or {}
    refresh = num(body.get("refresh_minutes"), 1, 60, "Refresh interval")
    back = num(cal.get("days_back"), 7, 400, "Days back")
    fwd = num(cal.get("days_ahead"), 7, 400, "Days ahead")
    lat = num(w.get("lat"), -90, 90, "Latitude", float)
    lon = num(w.get("lon"), -180, 180, "Longitude", float)
    wh = body.get("work_hours") or []
    if len(wh) != 2:
        raise ValueError("Work hours need a start and an end")
    h0, h1 = num(wh[0], 0, 23, "Work start"), num(wh[1], 1, 24, "Work end")
    if h1 <= h0:
        raise ValueError("Work hours must end after they start")
    with LOCK:
        cfg = harvest.load_config()
        cfg["refresh_minutes"] = refresh
        cfg.setdefault("calendar", {}).update(days_back=back, days_ahead=fwd)
        cfg.setdefault("weather", {}).update(enabled=bool(w.get("enabled")), place=str(w.get("place", "")).strip()[:60],
                                             lat=lat, lon=lon)
        cfg.setdefault("jira", {}).update(enabled=bool(j.get("enabled")), jql=str(j.get("jql", "")).strip()[:500])
        cfg.setdefault("stats", {})["work_hours"] = [h0, h1]
        tmp = HERE / "config.json.tmp"
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(HERE / "config.json")
    with WLOCK:
        WEATHER.update(t=0.0, key=None, data=None)   # place may have changed
    WAKE.set()                                       # timer restarts its wait with the new interval
    return {"settings": settings_payload(), "data": json.loads(with_kanban(run_harvest()))}


# ---------------------------------------------------------------- Plan blocks (KAN-61)

PLAN_CACHE = {}          # (from, to) -> (time, events, week load); events for the suggestions, short-lived
PLAN_TTL = 120
PLOCK = threading.Lock()


def plan_cfg() -> dict:
    p = harvest.load_config().get("planner") or {}
    return {"calendar": p.get("calendar", ""), "buffer": int(p.get("buffer_minutes", 15)),
            "outlook": bool(p.get("outlook", True)), "outlook_account": p.get("outlook_account", ""),
            "outlook_calendar": p.get("outlook_calendar", "Calendar"),
            "outlook_categories": [str(x) for x in p.get("outlook_categories", []) if str(x).strip()]}


def plan_categories(pc: dict):
    """Outlook categories on offer, and None or the reason they are not."""
    if not pc["outlook"]:
        return [], "Outlook categories are switched off (config planner.outlook)"
    try:
        return outlook.categories(pc["outlook_categories"]), None
    except Exception as e:
        harvest.log(f"plan: outlook categories failed: {type(e).__name__}: {e}")
        return [], f"{e}"


def plan_events(last: date):
    """History (for habits and the Ahead forecast) up to the planning horizon, cached for PLAN_TTL seconds."""
    now = datetime.now()
    monday = now.date() - timedelta(days=now.weekday())
    end = max(last, monday + timedelta(weeks=planner.MAX_WEEKS, days=-1))
    k = ((monday - timedelta(weeks=53)).isoformat(), end.isoformat())
    hit = PLAN_CACHE.get(k)
    if hit and time.time() - hit[0] < PLAN_TTL:
        return hit[1], hit[2]
    events = harvest.period_events_child(*k)
    st = harvest.load_config().get("stats") or {}
    try:
        wl = planner.week_load(ahead.forecast(events, st, now, weeks=planner.MAX_WEEKS + 1))
    except Exception as e:           # the forecast is a ranking hint; plan without it
        harvest.log(f"plan: forecast failed: {type(e).__name__}: {e}")
        wl = {}
    PLAN_CACHE.clear()
    PLAN_CACHE[k] = (time.time(), events, wl)
    return events, wl


def plan_suggest(body: dict) -> dict:
    pc = plan_cfg()
    now = datetime.now()
    st = harvest.load_config().get("stats") or {}
    cats, cat_err = plan_categories(pc)
    items = planner.clean_items(body.get("rows"), now.date(), stats.Rules(st), [c["name"] for c in cats])
    last = planner.horizon(items, now.date())
    until = str(body.get("until") or "")      # the day or week the page shows
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
        last = max(last, min(date.fromisoformat(until), now.date() + timedelta(weeks=planner.MAX_WEEKS)))
    events, wl = plan_events(last)
    out = planner.Planner(events, st, now, last, wl, pc["buffer"]).plan(items)
    out["calendar"] = pc["calendar"]
    out["categories"], out["outlook"] = cats, {"ok": cat_err is None, "error": cat_err,
                                               "calendar": pc["outlook_calendar"]}
    out["last_batch"] = last_batch_summary()
    return out


def plan_path() -> Path:
    return Path(harvest.load_config()["output_dir"]).expanduser() / "plan_batches.json"


def last_batch():
    try:
        return json.loads(plan_path().read_text(encoding="utf-8")).get("last")
    except (FileNotFoundError, ValueError):
        return None


WEATHER = {"t": 0.0, "key": None, "data": None}
WLOCK = threading.Lock()


def weather_payload() -> dict:
    """Greeting weather from Open-Meteo. Only the configured coordinates leave the Mac; cached 30 min,
    and a failed fetch keeps the last good answer so the greeting never flickers."""
    from urllib.parse import urlencode
    from urllib.request import urlopen
    w = harvest.load_config().get("weather") or {}
    if not w.get("enabled", True):
        return {"enabled": False}
    lat, lon = float(w.get("lat", 59.35)), float(w.get("lon", 18.07))
    key = (lat, lon)
    with WLOCK:
        if WEATHER["data"] and WEATHER["key"] == key and time.time() - WEATHER["t"] < 30 * 60:
            return WEATHER["data"]
        q = urlencode({"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
                       "current": "temperature_2m,weather_code,wind_speed_10m,is_day",
                       "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
        try:
            with urlopen("https://api.open-meteo.com/v1/forecast?" + q, timeout=6) as r:
                j = json.load(r)
            cur, day = j.get("current", {}), j.get("daily", {})
            first = lambda k: (day.get(k) or [None])[0]
            WEATHER.update(t=time.time(), key=key, data={
                "enabled": True, "place": str(w.get("place", "")),
                "temp": cur.get("temperature_2m"), "code": cur.get("weather_code"), "wind": cur.get("wind_speed_10m"),
                "is_day": cur.get("is_day", 1), "max": first("temperature_2m_max"), "min": first("temperature_2m_min"),
                "rain": first("precipitation_probability_max")})
        except Exception as e:
            harvest.log(f"weather failed: {type(e).__name__}: {e}")
            if not (WEATHER["data"] and WEATHER["key"] == key):
                return {"enabled": True, "error": "Weather unavailable"}
        return WEATHER["data"]


def last_batch_summary():
    b = last_batch()
    return {"batch": b["batch"], "at": b["at"], "calendar": b["calendar"], "count": len(b["created"])} if b else None


def save_last_batch(b):
    tmp = plan_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"last": b}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(plan_path())


def clean_blocks(body: dict) -> list:
    blocks = body.get("blocks")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 40:
        raise ValueError("Nothing to book")
    out = []
    for n, b in enumerate(blocks, 1):
        title = str(b.get("title") or "").strip() or "Blocked time"
        s, e = str(b.get("start") or ""), str(b.get("end") or "")
        try:
            ds, de = datetime.strptime(s, "%Y-%m-%dT%H:%M"), datetime.strptime(e, "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError(f"Block {n}: no time chosen")
        if not timedelta(minutes=15) <= de - ds <= timedelta(hours=10):
            raise ValueError(f"Block {n}: length must be 15 min to 10 h")
        if ds < datetime.now() - timedelta(minutes=5):
            raise ValueError(f"Block {n}: the time has passed")
        out.append({"unit": str(b.get("unit") or n)[:40], "title": title[:300], "notes": str(b.get("notes") or "").strip()[:4000],
                    "category": str(b.get("category") or "").strip()[:100], "start": s, "end": e,
                    "obsnote": str(b.get("obsnote") or "").strip()[:120]})
    return out


def plan_notes(blocks: list, created: list, calendar: str) -> list:
    """v0.55 (KAN-97): an Obsidian note for each booked block that asked for one. The Meeting template makes the
    series' meeting note ('YYYY-MM-DD Title', event: <title>); any other template makes a note named after the block
    with meeting: <title>, so both show under Related notes. Create-only; Undo of the blocks leaves the notes."""
    want = [c for c in created if blocks[c["i"]].get("obsnote")]
    if not want:
        return []
    cfg = harvest.load_config()
    ids = {t["id"] for t in notes.template_list(cfg)}
    out = []
    for c in want:
        b = blocks[c["i"]]
        tpl, row = b["obsnote"], {"i": c["i"], "unit": b["unit"]}
        try:
            if tpl not in ids:
                raise ValueError("unknown template")
            meeting = tpl == "meeting"
            ev = {"title": b["title"], "start": b["start"], "end": b["end"], "calendar": calendar}
            with NLOCK:
                r = notes.create(cfg, notes.default_folder(cfg, "meeting" if meeting else "note"),
                                 f"{b['start'][:10]} {b['title']}" if meeting else b["title"], tpl, event=ev)
            row.update(path=r["path"], uri=r["uri"])
            harvest.log(f"plan note created: {r['path']}")
        except (ValueError, OSError) as e:
            row["error"] = f"{b['title']}: note not created ({e})"
        out.append(row)
    return out


def plan_book(body: dict) -> dict:
    """Check against a fresh calendar read, then create. Conflicts stop the booking unless force is set."""
    blocks = clean_blocks(body)
    days = sorted(b["start"][:10] for b in blocks) + sorted(b["end"][:10] for b in blocks)
    with PLOCK:                      # two clicks never book twice in parallel
        events = harvest.period_events_child(days[0], days[-1])
        st = harvest.load_config().get("stats") or {}
        clash = planner.conflicts(events, blocks, st)
        # blocks of this batch overlapping each other
        for i, a in enumerate(blocks):
            for b in blocks[:i]:
                if a["start"] < b["end"] and b["start"] < a["end"]:
                    clash.append({"i": i, "with": [b["title"] + " (in this plan)"]})
        if clash and not body.get("force"):
            return {"conflicts": [dict(c, unit=blocks[c["i"]]["unit"]) for c in clash]}
        batch = time.strftime("%Y%m%d-%H%M%S")
        pc, cal_id = plan_cfg(), str(body.get("calendar_id") or "")
        cal = next((c for c in (json.loads(data_path().read_text(encoding="utf-8")).get("calendar") or {}).get("calendars", [])
                    if c.get("id") == cal_id), None)
        # the Outlook calendar goes through Outlook, so the category is set; anything else through EventKit
        route = "outlook" if pc["outlook"] and cal and cal.get("exchange") and cal.get("name") == pc["outlook_calendar"] \
            and plan_categories(pc)[1] is None else "eventkit"
        if route == "outlook":
            res = outlook.create_events(blocks, pc["outlook_account"], pc["outlook_calendar"],
                                        f"{harvest.PLAN_TAG}, batch {batch}")
        else:
            res = harvest.add_events_child({"calendar_id": cal_id, "batch": batch, "blocks": blocks})
            res["warnings"] = [{"i": i, "warning": "category not set (only possible in the Outlook calendar)"}
                               for i, b in enumerate(blocks) if b["category"]]
        for c in res["created"]:
            c["unit"] = blocks[c["i"]]["unit"]
        for c in res["errors"] + res["warnings"]:
            c["unit"] = blocks[c["i"]]["unit"]
        if res["created"]:
            save_last_batch({"batch": batch, "at": time.strftime("%Y-%m-%dT%H:%M"), "calendar": res["calendar"],
                             "route": route, "created": res["created"]})
        PLAN_CACHE.clear()
        res["notes"] = plan_notes(blocks, res["created"], res["calendar"])
    harvest.log(f"plan: {len(res['created'])} block(s) created in '{res['calendar']}' via {route}, {len(res['errors'])} failed")
    data = json.loads(with_kanban(run_harvest()))
    data["booked"] = dict(res, batch=batch if res["created"] else None)
    data["last_batch"] = last_batch_summary()
    return data


def plan_undo(body: dict) -> dict:
    with PLOCK:
        b = last_batch()
        if not b or str(body.get("batch")) != b["batch"]:
            raise ValueError("Only the last booked batch can be undone")
        ids = [c["id"] for c in b["created"]]
        res = outlook.remove_events(ids, harvest.PLAN_TAG) if b.get("route") == "outlook" else harvest.remove_events_child(ids)
        plan_path().unlink(missing_ok=True)
        PLAN_CACHE.clear()
    harvest.log(f"plan: undo batch {b['batch']}: {len(res['removed'])} removed, {len(res['skipped'])} skipped")
    data = json.loads(with_kanban(run_harvest()))
    data["undone"] = res
    data["last_batch"] = None
    return data


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def _same_origin(self) -> bool:
        # Block other web pages from posting to this local server
        # Behind `tailscale serve` the page is https://<mac>.ts.net, so accept https and the forwarded host too
        origin = self.headers.get("Origin")
        hosts = {h for h in (self.headers.get("Host"), self.headers.get("X-Forwarded-Host")) if h}
        return origin is None or origin in {f"{s}://{h}" for s in ("http", "https") for h in hosts}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/stamp":   # v0.44: tiny poll, changes whenever the snapshot is rewritten
            p, w = data_path(), workplace_path()   # v0.46: a WFH mark on another device counts as a change
            self._json({"stamp": sum(x.stat().st_mtime_ns for x in (p, w) if x.exists())})
        elif path == "/data.json":
            p = data_path()
            if not p.exists():
                run_harvest()
            self._send(with_kanban(json.loads(p.read_text(encoding="utf-8"))), "application/json; charset=utf-8")
        elif path == "/manifest.webmanifest":
            self._send(json.dumps(MANIFEST).encode(), "application/manifest+json")
        elif path in ("/apple-touch-icon.png", "/icon.png"):
            self._send(ICON_PNG, "image/png")
        elif path == "/api/stats":
            from urllib.parse import parse_qs
            try:
                self._json(stats_payload(parse_qs(self.path.partition("?")[2])))
            except ValueError as e:
                self._json({"error": f"Bad period: {e}"}, 400)
            except Exception as e:
                harvest.log(f"stats failed: {type(e).__name__}: {e}")
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/api/ahead":
            from urllib.parse import parse_qs
            try:
                self._json(ahead_payload(parse_qs(self.path.partition("?")[2])))
            except ValueError as e:
                self._json({"error": f"Bad request: {e}"}, 400)
            except Exception as e:
                harvest.log(f"ahead failed: {type(e).__name__}: {e}")
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/api/weather":
            self._json(weather_payload())
        elif path == "/api/plan/last":
            self._json({"last_batch": last_batch_summary()})
        elif path == "/api/reminders/last":
            self._json({"last": rem_last_summary()})
        elif path == "/api/reminders/days":
            from urllib.parse import parse_qs
            try:
                self._json(rem_days(parse_qs(self.path.partition("?")[2])))
            except Exception as e:
                harvest.log(f"reminder days failed: {type(e).__name__}: {e}")
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/api/profiles":
            self._json(load_profiles())
        elif path == "/api/settings":
            self._json(settings_payload())
        elif path == "/api/search":
            from urllib.parse import parse_qs
            q = (parse_qs(self.path.partition("?")[2]).get("q") or [""])[0][:200]
            try:
                self._json(search_notes(q))
            except Exception as e:
                harvest.log(f"search failed: {type(e).__name__}: {e}")
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/api/timer":
            self._json({"timer": timer_get()})
        elif path == "/api/alarms":
            self._json(dict(ALARMS.state(), snooze=(harvest.load_config().get("alarms") or {}).get("snooze_minutes", 5)))
        elif path == "/api/jira/epics":
            try:
                cfg = harvest.load_config()
                self._json({"epics": harvest.jira_epics(cfg) if cfg.get("jira", {}).get("enabled") else [], "enabled": bool(cfg.get("jira", {}).get("enabled")),
                            "default": cfg.get("jira", {}).get("default_epic", "")})
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/api/inbox":
            try:
                last = json.loads(moves_path().read_text(encoding="utf-8"))[-1:] if moves_path().exists() else []
                self._json(dict(notes.inbox_list(harvest.load_config()), last=last[0] if last else None))
            except ValueError as e:
                self._json({"error": str(e)}, 400)
        elif path == "/api/notes/targets":
            self._json(notes_targets())
        elif path == "/api/vault":
            try:
                self._json(harvest.vault_tree(harvest.load_config()))
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._same_origin():
            return self._send(b"forbidden", "text/plain", 403)
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if path == "/refresh":
                return self._send(with_kanban(run_harvest()), "application/json; charset=utf-8")
            if path == "/api/calendar/show":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}.*", str(body.get("start", ""))):
                    return self._json({"error": "bad request"}, 400)
                return self._json(show_in_calendar(body["start"]))
            if path == "/api/kanban":
                cols = ("backlog", "todo", "doing", "done", None, "", "auto")
                if not isinstance(body.get("id"), str) or body.get("col") not in cols:
                    return self._json({"error": "bad request"}, 400)
                return self._json({"kanban": set_kanban(body["id"], body.get("col"))})
            if path == "/api/wfh":
                try:
                    return self._json(set_workplace(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/jira/move":
                # Board drag on a Jira card: change the real status, keep Backlog/To Do as a local split
                cat = {"backlog": "new", "todo": "new", "delegated": "new", "doing": "indeterminate", "done": "done"}.get(body.get("col"))
                key = str(body.get("key", ""))
                if not cat or not key:
                    return self._json({"error": "bad request"}, 400)
                prefer = {"backlog": "Backlog", "todo": "To Do", "delegated": ("Delegated", "Delagated")}.get(body["col"], "")
                got = harvest.jira_move(harvest.load_config(), key, cat, prefer)
                if body["col"] == "delegated" and got.replace(" ", "").lower() not in ("delegated", "delagated"):
                    return self._json({"error": f"{key}: no transition to a Delegated status in Jira"}, 400)
                # v0.50 (KAN-91): the `delegated` label follows the column
                cfg = harvest.load_config()
                if body["col"] == "delegated":
                    harvest.jira_labels(cfg, key, add=[harvest.DELEG_LABEL])
                else:
                    harvest.jira_labels(cfg, key, remove=[harvest.DELEG_LABEL])
                set_kanban("jira:" + key, body["col"] if body["col"] in ("backlog", "todo") else None)
                return self._send(with_kanban(run_harvest()), "application/json; charset=utf-8")
            if path == "/api/jira/hold":   # v0.50 (KAN-91): add the `hold` label, or remove hold/onhold/on-hold
                key = str(body.get("key", ""))
                if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key) or not isinstance(body.get("hold"), bool):
                    return self._json({"error": "bad request"}, 400)
                cfg = harvest.load_config()
                if body["hold"]:
                    harvest.jira_labels(cfg, key, add=["hold"])
                else:
                    harvest.jira_labels(cfg, key, remove=sorted(harvest.HOLD_LABELS))
                harvest.log(f"jira: {'hold' if body['hold'] else 'resume'} {key}")
                data = json.loads(with_kanban(run_harvest()))
                iss = next((i for i in (data.get("jira") or {}).get("issues", []) if i["key"] == key), None)
                if not body["hold"] and iss and iss.get("hold"):
                    data["warn"] = f"{key} is still on hold: the #hold mark is in its description. Remove it in Jira."
                return self._json(data)
            if path == "/api/reminders/hold":
                try:
                    return self._json(rem_hold(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path in ("/api/reminders/undo", "/api/reminders") and (path.endswith("undo") or "items" in body):
                try:
                    return self._json((rem_undo if path.endswith("undo") else rem_add)(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/reminders" and "titles" in body:
                try:
                    items = clean_reminders(body)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                res = harvest.add_reminders_child(items)["results"]
                ok = [r for r in res if r.get("ok")]
                harvest.log(f"reminders batch: {len(ok)} of {len(res)} created")
                data = json.loads(with_kanban(run_harvest()))   # one re-sync for the whole batch
                data["batch"] = res
                return self._json(data)
            if path == "/api/reminders":
                try:
                    item = clean_reminder(body)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                created = harvest.add_reminder_child(item)
                harvest.log(f"reminder created in '{created['list']}'")
                data = json.loads(with_kanban(run_harvest()))
                data["created"] = created
                return self._json(data)
            if path == "/api/stats/vacation":
                try:
                    return self._json({"vacation_weeks": set_vacation(str(body.get("week", "")), bool(body.get("on")))})
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path.startswith("/api/plan/"):
                fn = {"/api/plan/suggest": plan_suggest, "/api/plan/book": plan_book, "/api/plan/undo": plan_undo}.get(path)
                if fn:
                    try:
                        return self._json(fn(body))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                    except Exception as e:
                        harvest.log(f"{path} failed: {type(e).__name__}: {e}")
                        return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            if path in ("/api/profiles", "/api/settings"):
                try:
                    return self._json((save_profiles if path == "/api/profiles" else save_settings)(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path in ("/api/jira/issue", "/api/timer", "/api/capture", "/api/inbox/move", "/api/inbox/undo"):
                fn = {"/api/jira/issue": jira_issue, "/api/timer": timer_post, "/api/inbox/move": inbox_move,
                      "/api/inbox/undo": lambda b: inbox_undo(),
                      "/api/capture": lambda b: dict(notes.capture(harvest.load_config(), b.get("text"), bool(b.get("task"))))}[path]
                try:
                    out = fn(body)
                    if path == "/api/capture":
                        harvest.log(f"captured to {out['path']}")
                        harvest_soon()
                    return self._json(out)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except Exception as e:
                    harvest.log(f"{path} failed: {type(e).__name__}: {e}")
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            if path == "/api/alarms":
                try:
                    out = ALARMS.act(body)
                    if body.get("action") == "add":
                        harvest.log(f"alarm set: {out['alarm']['label']} ({out['alarm']['minutes']:g} min)")
                    return self._json(out)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/notes":
                try:
                    return self._json(note_create(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except OSError as e:
                    harvest.log(f"note create failed: {type(e).__name__}: {e}")
                    return self._json({"error": f"Could not write the note: {e}"}, 500)
            if path == "/api/review/auto":
                try:
                    return self._json(review_auto(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                except OSError as e:
                    harvest.log(f"weekly review write failed: {type(e).__name__}: {e}")
                    return self._json({"error": f"Could not write the review: {e}"}, 500)
            if path == "/api/obsidian":
                save_obsidian_selection(body)
                return self._send(with_kanban(run_harvest()), "application/json; charset=utf-8")
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        self._send(b"not found", "text/plain", 404)

    def log_message(self, *args):
        pass  # keep logs quiet


auth.install(Handler)  # KAN-42 access token


WAKE = threading.Event()   # set when Settings changes the refresh interval


def obsidian_watch_loop():
    """v0.44: notice hand edits in the Obsidian scope (stat only, every obsidian.watch_seconds, default 10;
    0 = off) and re-read just the Obsidian part, so an edited note or task shows within seconds."""
    last = None
    while True:
        try:
            cfg = harvest.load_config()
            every = float(cfg["obsidian"].get("watch_seconds", 10) or 0)
            if every <= 0 or not harvest.obsidian_on(cfg):
                time.sleep(60)
                continue
            sig = harvest.obsidian_signature(cfg)
            if last is not None and sig != last:
                with LOCK:
                    harvest.refresh_obsidian()
                harvest.log("obsidian changed; notes and tasks re-read")
            last = sig
        except Exception:
            traceback.print_exc()
            every = 60
        time.sleep(every)


def timer_loop():
    while True:
        try:
            run_harvest()
        except Exception:
            traceback.print_exc()
        # a wake-up only restarts the wait with the new interval; Settings has just harvested itself
        while WAKE.wait(float(harvest.load_config().get("refresh_minutes", 5)) * 60):
            WAKE.clear()


def main():
    cfg = harvest.load_config()
    harvest.log(f"service started (pid {os.getpid()})")
    threading.Thread(target=timer_loop, daemon=True).start()
    threading.Thread(target=alarm_loop, daemon=True).start()
    threading.Thread(target=obsidian_watch_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), Handler)
    print(f"Work dashboard on http://127.0.0.1:{cfg['port']}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
