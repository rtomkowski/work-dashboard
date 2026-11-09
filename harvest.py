"""Harvest Calendar, Reminders, Obsidian and (optionally) Jira into one JSON snapshot.

Run directly once from Terminal to trigger the macOS permission prompts:
    .venv/bin/python harvest.py
"""
from __future__ import annotations

import base64
import hashlib
import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(HERE / "config.json", encoding="utf-8") as f:
        return json.load(f)


def iso(dt):
    # Naive local time, always with a time part so the browser parses it as local
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else None


def log(msg: str):
    """Timestamped line in logs/out.log, so the next incident leaves evidence."""
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- EventKit

def _ek():
    import EventKit
    import Foundation
    return EventKit, Foundation


def _nsdate(dt: datetime):
    _, F = _ek()
    return F.NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _pydate(nsd):
    return datetime.fromtimestamp(nsd.timeIntervalSince1970()) if nsd is not None else None


def _hex_color(cal):
    try:
        import AppKit
        c = cal.color().colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
        return "#%02x%02x%02x" % tuple(
            int(round(x * 255)) for x in (c.redComponent(), c.greenComponent(), c.blueComponent()))
    except Exception:
        return None


def _request_access(store, kind: str) -> bool:
    """Block until the user/TCC answers. Completion fires on a background queue."""
    EK, _ = _ek()
    done, box = threading.Event(), {}

    def cb(granted, _err):
        box["ok"] = bool(granted)
        done.set()

    if kind == "events":
        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):  # macOS 14+
            store.requestFullAccessToEventsWithCompletion_(cb)
        else:
            store.requestAccessToEntityType_completion_(EK.EKEntityTypeEvent, cb)
    else:
        if hasattr(store, "requestFullAccessToRemindersWithCompletion_"):
            store.requestFullAccessToRemindersWithCompletion_(cb)
        else:
            store.requestAccessToEntityType_completion_(EK.EKEntityTypeReminder, cb)
    done.wait(120)
    return box.get("ok", False)


def _declined_by_me(ev) -> bool:
    try:
        for a in ev.attendees() or []:
            if a.isCurrentUser():
                return a.participantStatus() == 3  # EKParticipantStatusDeclined
    except Exception:
        pass
    return False


EK_CAL_SUBSCRIPTION = 3  # EKCalendarTypeSubscription


def _mirror_cal_ids(cals) -> set:
    """Calendars whose events may be copies of events elsewhere (v0.31, KAN-66): subscribed calendars, and
    calendars whose name occurs more than once (the same subscription added in two accounts). EventKit returns
    calendars hidden in Calendar.app too, so such copies show up even when Calendar.app looks clean."""
    names = Counter(k.title() for k in cals)
    return {str(k.calendarIdentifier()) for k in cals
            if k.type() == EK_CAL_SUBSCRIPTION or names[k.title()] > 1}


def _event_key(e):
    return ((e.title() or "").strip().casefold(), e.startDate().timeIntervalSince1970(),
            e.endDate().timeIntervalSince1970(), bool(e.isAllDay()))


def _drop_mirror_copies(pairs, mirror_ids: set):
    """pairs: (EKEvent, item). Events with the same title, start, end and all-day flag collapse to one when at
    least one copy comes from a mirror calendar; a copy in an ordinary calendar wins. Two ordinary events that
    happen to match are both kept. Returns (items, number dropped)."""
    is_mirror = lambda e: str(e.calendar().calendarIdentifier()) in mirror_ids
    kept, seen, dropped = [], {}, 0
    for e, item in sorted(pairs, key=lambda p: is_mirror(p[0])):  # stable: ordinary calendars first
        k, m = _event_key(e), is_mirror(e)
        if k in seen and (m or seen[k]):
            dropped += 1
            continue
        seen.setdefault(k, m)
        kept.append(item)
    return kept, dropped


def _feed_cal_ids(cals, cfg: dict) -> set:
    """Course schedule feeds (v0.32, KAN-66): calendars whose name matches calendar.schedule_feeds (fnmatch,
    default the KTH term suffixes "*-HT??" and "*-VT??"). Such feeds may list each session twice (Swedish and
    English title) and repeat sessions you already have in your own calendar."""
    pats = cfg["calendar"].get("schedule_feeds", ["*-HT??", "*-VT??"])
    return {str(k.calendarIdentifier()) for k in cals if any(fnmatch.fnmatchcase(k.title(), p) for p in pats)}


def _collapse_feeds(pairs, feed_ids: set, yield_to_own: bool = False):
    """pairs: (EKEvent, item). Course feeds (v0.32, v0.34) list each session twice (Swedish and English title):
    a feed shows one timed event per slot, preferring a copy with a location (the room). Your own entries are
    never touched. With yield_to_own (Statistics, Ahead), a feed session is also left out when your own event has
    the same start and end and its title contains the course code (calendar name before the first "-", e.g.
    MG2045), so the time counts once. All-day feed events stay. Returns (pairs, number dropped)."""
    if not feed_ids:
        return pairs, 0
    cid = lambda e: str(e.calendar().calendarIdentifier())
    slot = lambda e: (e.startDate().timeIntervalSince1970(), e.endDate().timeIntervalSince1970())
    owned = set()  # (slot, course code) covered by your own entry
    if yield_to_own:
        codes = {e.calendar().title().split("-")[0].strip().casefold() for e, _ in pairs if cid(e) in feed_ids}
        for e, _ in pairs:
            if cid(e) not in feed_ids and not e.isAllDay():
                t = (e.title() or "").casefold()
                owned |= {(slot(e), c) for c in codes if c and c in t}
    kept, seen, dropped = [], set(), 0
    rank = lambda p: (not (p[0].location() or "").strip(), p[0].title() or "")  # with room first, then title
    for e, item in sorted(pairs, key=rank):
        if cid(e) in feed_ids and not e.isAllDay():
            k = (e.calendar().title(),) + slot(e)  # by name: KTH may give one feed per language (v0.35)
            if k in seen or (slot(e), e.calendar().title().split("-")[0].strip().casefold()) in owned:
                dropped += 1
                continue
            seen.add(k)
        kept.append((e, item))
    return kept, dropped

def _filter_cals(cals) -> list:
    """Calendar list for the sidebar filters (keyed by name): one entry per name for read-only calendars,
    ordinary before subscribed; writable calendars all stay, Plan blocks picks them by id."""
    out, names = [], set()
    for k in sorted(cals, key=lambda k: k.type() == EK_CAL_SUBSCRIPTION):
        if not k.allowsContentModifications() and k.title() in names:
            continue
        names.add(k.title()); out.append(k)
    return out


def harvest_calendar(store, cfg: dict, now: datetime) -> dict:
    EK, _ = _ek()
    if not _request_access(store, "events"):
        raise PermissionError("Calendar access denied (System Settings > Privacy & Security > Calendars)")
    c = cfg["calendar"]
    day0 = datetime.combine(now.date(), datetime.min.time())
    start = day0 - timedelta(days=c["days_back"])
    end = day0 + timedelta(days=c["days_ahead"] + 1)
    excl = set(c.get("exclude_calendars", []))
    cals = [k for k in store.calendarsForEntityType_(EK.EKEntityTypeEvent) if k.title() not in excl]
    if not cals:
        return {"events": [], "calendars": []}
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(start), _nsdate(end), cals)
    pairs = []
    for e in store.eventsMatchingPredicate_(pred) or []:
        if _declined_by_me(e):
            continue
        pairs.append((e, {
            "title": e.title() or "(no title)",
            "start": iso(_pydate(e.startDate())),
            "end": iso(_pydate(e.endDate())),
            "all_day": bool(e.isAllDay()),
            "calendar": e.calendar().title(),
            "location": e.location() or "",
            "free": e.availability() == 1,  # EKEventAvailabilityFree
            "rec": bool(e.hasRecurrenceRules()),  # v0.70: Outlook category matching by series
            **_ev_detail(e),
        }))
    pairs, folded = _collapse_feeds(pairs, _feed_cal_ids(cals, cfg))
    events, merged = _drop_mirror_copies(pairs, _mirror_cal_ids(cals))
    merged += folded
    events.sort(key=lambda x: x["start"] or "")
    return {
        "events": events,
        "calendars": [_cal_info(k) for k in _filter_cals(cals)],
        "merged_copies": merged,
    }


# Event detail panel (v0.36, KAN-67): what the Day view shows when an event is clicked. Stays in the local snapshot.
NOTES_MAX, ATTENDEES_MAX = 3000, 60
FREQ = {0: "daily", 1: "weekly", 2: "monthly", 3: "yearly"}  # EKRecurrenceFrequency
STATUS = {0: "unknown", 1: "pending", 2: "accepted", 3: "declined", 4: "tentative", 5: "delegated"}
ROLE = {1: "required", 2: "optional", 3: "chair", 4: "info"}  # EKParticipantRole


def _person(p) -> dict:
    url = p.URL()
    mail = str(url.resourceSpecifier() or "") if url is not None and str(url.scheme() or "") == "mailto" else ""
    return {"name": p.name() or mail or "(unknown)", "email": mail, "me": bool(p.isCurrentUser())}


def _ev_detail(e) -> dict:
    """id (stable across refreshes, one per occurrence), notes, url, organizer, attendees, recurrence."""
    out = {}
    try:
        out["id"] = hashlib.sha1(f"{e.eventIdentifier() or e.title()}|{e.startDate().timeIntervalSince1970()}".encode()).hexdigest()[:12]
        notes = (e.notes() or "").replace("\r\n", "\n").strip()
        out["notes"] = notes[:NOTES_MAX] + ("…" if len(notes) > NOTES_MAX else "")
        u = e.URL()
        out["url"] = str(u.absoluteString()) if u is not None else ""
        o = e.organizer()
        out["organizer"] = _person(o) if o is not None else None
        people = []
        for a in e.attendees() or []:
            if a.participantType() in (2, 3):  # room or resource: the location already says it
                continue
            people.append({**_person(a), "status": STATUS.get(a.participantStatus(), "unknown"),
                           "role": ROLE.get(a.participantRole(), "")})
        out["attendees_total"] = len(people)
        out["attendees"] = people[:ATTENDEES_MAX]
        rules = (e.recurrenceRules() or []) if e.hasRecurrenceRules() else []
        if rules:
            r = rules[0]
            out["repeat"] = {"freq": FREQ.get(r.frequency(), ""), "every": int(r.interval() or 1)}
    except Exception as ex:  # details are optional; the event itself must still show
        out.setdefault("id", hashlib.sha1(f"{e.title()}|{e.startDate()}".encode()).hexdigest()[:12])
        log(f"event detail skipped: {ex}")
    return out


def _cal_info(cal) -> dict:
    """Name and colour for the filters; id, account and writability for Plan blocks (KAN-61)."""
    src = cal.source()
    return {"name": cal.title(), "color": _hex_color(cal), "id": str(cal.calendarIdentifier()),
            "account": src.title() if src is not None else "", "exchange": src is not None and src.sourceType() == 1,
            "writable": bool(cal.allowsContentModifications())}


def _my_status(ev):
    """Own participant status: 2 accepted, 3 declined, 4 tentative (EKParticipantStatus); None if not an invitation."""
    try:
        for a in ev.attendees() or []:
            if a.isCurrentUser():
                return a.participantStatus()
    except Exception:
        pass
    return None


def period_events(store, cfg: dict, start: datetime, end: datetime) -> list:
    """Calendar events in [start, end) for the statistics view (KAN-54): title, times, all-day and availability.
    Same calendars as the dashboard plus stats.exclude_calendars; declined invitations are left out."""
    EK, _ = _ek()
    if not _request_access(store, "events"):
        raise PermissionError("Calendar access denied (System Settings > Privacy & Security > Calendars)")
    excl = set(cfg["calendar"].get("exclude_calendars", [])) | set((cfg.get("stats") or {}).get("exclude_calendars", []))
    cals = [k for k in store.calendarsForEntityType_(EK.EKEntityTypeEvent) if k.title() not in excl]
    if not cals:
        return []
    out = []
    # EventKit only searches up to 4 years per predicate; step through in 1-year chunks anyway
    a = start
    while a < end:
        b = min(end, a + timedelta(days=366))
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(_nsdate(a), _nsdate(b), cals)
        for e in store.eventsMatchingPredicate_(pred) or []:
            st = _my_status(e)
            if st == 3:
                continue
            av = e.availability()  # -1 n/a, 0 busy, 1 free, 2 tentative, 3 unavailable (Exchange 'out of office')
            avail = {1: "free", 2: "tentative", 3: "oof"}.get(av, "busy")
            if st == 4 and avail == "busy":
                avail = "tentative"
            try:
                created = iso(_pydate(e.creationDate()))   # used by the Ahead forecast (KAN-55); may be missing
            except Exception:
                created = None
            out.append((e, {"id": str(e.eventIdentifier()) + "|" + iso(_pydate(e.startDate())),
                        "title": e.title() or "", "start": iso(_pydate(e.startDate())), "end": iso(_pydate(e.endDate())),
                        "all_day": bool(e.isAllDay()), "avail": avail, "created": created,
                        "rec": bool(e.hasRecurrenceRules())}))   # v0.67: recurring, for Outlook category matching
        a = b
    # an event spanning a chunk border is returned twice
    seen, uniq = set(), []
    for e, x in out:
        if x["id"] not in seen:
            seen.add(x["id"]); uniq.append((e, x))
    uniq = _collapse_feeds(uniq, _feed_cal_ids(cals, cfg), yield_to_own=True)[0]  # course feeds: count once (v0.34)
    return _drop_mirror_copies(uniq, _mirror_cal_ids(cals))[0]  # same subscription in two accounts (v0.31)


def period_events_child(start: str, end: str, timeout: int = 150) -> list:
    """Read the period in a fresh process (same reason as eventkit_child)."""
    r = subprocess.run([sys.executable, str(HERE / "harvest.py"), "--period-events", start, end],
                       capture_output=True, text=True, timeout=timeout, cwd=HERE)
    lines = r.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(lines[-1])
    if isinstance(out, dict) and out.get("error"):
        raise RuntimeError(out["error"])
    return out


def _fetch_reminders(store, pred) -> list:
    done, box = threading.Event(), {}

    def cb(rems):
        box["r"] = list(rems or [])
        done.set()

    store.fetchRemindersMatchingPredicate_completion_(pred, cb)
    done.wait(120)
    return box.get("r", [])


def harvest_reminders(store, cfg: dict, now: datetime) -> dict:
    EK, F = _ek()
    if not _request_access(store, "reminders"):
        raise PermissionError("Reminders access denied (System Settings > Privacy & Security > Reminders)")
    excl = set(cfg["reminders"].get("exclude_lists", []))
    cals = [k for k in store.calendarsForEntityType_(EK.EKEntityTypeReminder) if k.title() not in excl]
    if not cals:
        return {"open": [], "done_7d": 0, "lists": []}
    ns_cal = F.NSCalendar.currentCalendar()

    open_items = []
    pred = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, cals)
    for r in _fetch_reminders(store, pred):
        comps = r.dueDateComponents()
        due, has_time = None, False
        if comps is not None:
            due = _pydate(ns_cal.dateFromComponents_(comps))
            has_time = comps.hour() != F.NSDateComponentUndefined
        open_items.append({
            "id": "rem:" + str(r.calendarItemIdentifier()),
            "title": r.title() or "(untitled)",
            "list": r.calendar().title(),
            "due": iso(due),
            "has_time": has_time,
            "priority": int(r.priority() or 0),  # 1 high, 5 medium, 9 low, 0 none
            "notes": (r.notes() or "")[:200],
            "hold": is_on_hold(r.notes()),   # v0.47: #hold, #onhold, #on-hold or @hold anywhere in the notes
            "writable": bool(r.calendar().allowsContentModifications()),
        })

    since = now - timedelta(days=max(7, int(cfg["reminders"].get("completed_days", 14))))
    pred_done = store.predicateForCompletedRemindersWithCompletionDateStarting_ending_calendars_(
        _nsdate(since), _nsdate(now), cals)
    done_list = _fetch_reminders(store, pred_done)
    week_ago = now - timedelta(days=7)
    done_7d = sum(1 for r in done_list if r.completionDate() is not None and _pydate(r.completionDate()) >= week_ago)
    completed = [{
        "id": "rem:" + str(r.calendarItemIdentifier()),
        "title": r.title() or "(untitled)",
        "list": r.calendar().title(),
        "completed": iso(_pydate(r.completionDate())),
    } for r in done_list]

    dflt = store.defaultCalendarForNewReminders()
    return {
        "open": open_items,
        "done_7d": done_7d,
        "completed": completed,
        "lists": [{"name": k.title(), "color": _hex_color(k), "writable": bool(k.allowsContentModifications())}
                  for k in cals],
        "default_list": dflt.title() if dflt is not None else None,
    }


# ---------------------------------------------------------------- Reminders: on hold (v0.47, KAN-88)
# A reminder is on hold when its notes carry #hold, #onhold, #on-hold or @hold (any case). Typed by hand in
# Reminders or set from the dashboard. Hold/Resume is the only edit the dashboard makes to an existing reminder,
# and it touches nothing but that marker in the notes: title, dates, list and the rest of the notes stay as they are.

HOLD_RE = re.compile(r"(?<![\w#@])[#@](?:on-?hold|hold)\b", re.IGNORECASE)
HOLD_MARK = "#hold"


def is_on_hold(notes) -> bool:
    return bool(HOLD_RE.search(notes or ""))


def _strip_hold(notes: str) -> str:
    out = HOLD_RE.sub("", notes or "")
    out = re.sub(r"[ \t]+\n", "\n", out)            # blanks left at line ends
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _with_hold(notes: str) -> str:
    notes = (notes or "").rstrip()
    return f"{notes}\n\n{HOLD_MARK}" if notes else HOLD_MARK


def set_hold(store, req: dict) -> dict:
    """req: {"ids": ["rem:..."], "hold": true|false}. Adds HOLD_MARK or strips every hold marker.
    Open reminders in writable lists only; already-right ones are left alone and reported."""
    if not _request_access(store, "reminders"):
        raise PermissionError("Reminders access denied (System Settings > Privacy & Security > Reminders)")
    want = bool(req.get("hold"))
    changed, skipped = [], []
    for rid in req.get("ids") or []:
        r = store.calendarItemWithIdentifier_(str(rid).removeprefix("rem:"))
        if r is None:
            skipped.append({"id": rid, "why": "not found"}); continue
        if r.isCompleted():
            skipped.append({"id": rid, "why": "already completed"}); continue
        if not r.calendar().allowsContentModifications():
            skipped.append({"id": rid, "why": f"list '{r.calendar().title()}' is read-only"}); continue
        cur = r.notes() or ""
        if is_on_hold(cur) == want:
            skipped.append({"id": rid, "why": "already " + ("on hold" if want else "active")}); continue
        new = _with_hold(cur) if want else _strip_hold(cur)
        r.setNotes_(new or None)
        res = store.saveReminder_commit_error_(r, True, None)
        ok, err = res if isinstance(res, tuple) else (res, None)
        if ok:
            changed.append(rid)
        else:
            skipped.append({"id": rid, "why": err.localizedDescription() if err is not None else "save failed"})
    return {"hold": want, "changed": changed, "skipped": skipped}


def set_hold_child(ids: list, hold: bool) -> dict:
    return _child("--set-hold", {"ids": ids, "hold": bool(hold)})


# ---------------------------------------------------------------- Reminders: create (v0.10, KAN-53)
# Create, plus undo of the dashboard's own last batch (v0.27). Nothing here edits or completes a reminder,
# and removal needs both the stored identifier and REM_TAG in the notes, like Plan blocks.

REM_TAG = "Created by WorkDashboard (reminders)"

def create_reminder(store, item: dict) -> dict:
    """item: title, list (name or ""), date "YYYY-MM-DD" or "", time "HH:MM" or "", priority 0/1/5/9, notes,
    optional tag (added as the last line of the notes, needed for undo)."""
    EK, F = _ek()
    if not _request_access(store, "reminders"):
        raise PermissionError("Reminders access denied (System Settings > Privacy & Security > Reminders)")
    lists = list(store.calendarsForEntityType_(EK.EKEntityTypeReminder))
    cal = next((k for k in lists if k.title() == item.get("list")), None) or store.defaultCalendarForNewReminders()
    if cal is None:
        raise ValueError("No Reminders list available")
    if not cal.allowsContentModifications():
        raise ValueError(f"List '{cal.title()}' is read-only")

    r = EK.EKReminder.reminderWithEventStore_(store)
    r.setTitle_(item["title"])
    r.setCalendar_(cal)
    notes = "\n\n".join(x for x in (item.get("notes") or "", item.get("tag") or "") if x)
    if notes:
        r.setNotes_(notes)
    if item.get("priority"):
        r.setPriority_(int(item["priority"]))
    if item.get("date"):
        y, m, d = (int(x) for x in item["date"].split("-"))
        comps = F.NSDateComponents.alloc().init()
        comps.setYear_(y); comps.setMonth_(m); comps.setDay_(d)
        if item.get("time"):
            hh, mm = (int(x) for x in item["time"].split(":"))
            comps.setHour_(hh); comps.setMinute_(mm)
            # Reminders.app only notifies when there is an alarm; add one at the due time
            r.addAlarm_(EK.EKAlarm.alarmWithAbsoluteDate_(_nsdate(datetime(y, m, d, hh, mm))))
        r.setDueDateComponents_(comps)

    res = store.saveReminder_commit_error_(r, True, None)
    ok, err = res if isinstance(res, tuple) else (res, None)
    if not ok:
        raise RuntimeError(f"Save failed: {err.localizedDescription() if err is not None else 'unknown error'}")
    return {"id": "rem:" + str(r.calendarItemIdentifier()), "list": cal.title(), "title": item["title"]}


def create_reminders(store, items: list) -> dict:
    """Batch quick-add: one store, one access check, then each item on its own. A failed item does not stop
    the others; every item gets a result so the page can keep exactly the lines that were not added."""
    results = []
    for item in items:
        try:
            results.append({"ok": True, **create_reminder(store, item)})
        except PermissionError:
            raise  # no access means none can be added; report it once
        except Exception as e:
            results.append({"ok": False, "title": item.get("title", ""), "error": f"{type(e).__name__}: {e}"})
    return {"results": results}


def remove_reminders(store, ids: list) -> dict:
    """Undo: remove only open reminders that still carry REM_TAG. Anything else is left alone and reported."""
    if not _request_access(store, "reminders"):
        raise PermissionError("Reminders access denied (System Settings > Privacy & Security > Reminders)")
    removed, skipped = [], []
    for rid in ids:
        r = store.calendarItemWithIdentifier_(rid.removeprefix("rem:"))
        if r is None:
            skipped.append({"id": rid, "why": "already gone"})
            continue
        if REM_TAG not in (r.notes() or ""):
            skipped.append({"id": rid, "why": "not created by the dashboard"})
            continue
        if r.isCompleted():
            skipped.append({"id": rid, "why": "already completed"})
            continue
        res = store.removeReminder_commit_error_(r, True, None)
        ok, err = res if isinstance(res, tuple) else (res, None)
        if ok:
            removed.append(rid)
        else:
            skipped.append({"id": rid, "why": err.localizedDescription() if err is not None else "remove failed"})
    return {"removed": removed, "skipped": skipped}


def remove_reminders_child(ids: list) -> dict:
    return _child("--remove-reminders", ids)


def add_reminders_child(items: list) -> dict:
    """Run create_reminders in a fresh process. Same rule as add_reminder_child: never retried automatically."""
    r = subprocess.run([sys.executable, str(HERE / "harvest.py"), "--add-reminders"], input=json.dumps(items),
                       capture_output=True, text=True, timeout=60 + 5 * len(items), cwd=HERE)
    lines = r.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(lines[-1])
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out


def add_reminder_child(item: dict, timeout: int = 60) -> dict:
    """Run create_reminder in a fresh process (same reason as eventkit_child). No in-process fallback:
    a retry after an unclear failure could create the reminder twice."""
    r = subprocess.run([sys.executable, str(HERE / "harvest.py"), "--add-reminder"], input=json.dumps(item),
                       capture_output=True, text=True, timeout=timeout, cwd=HERE)
    lines = r.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(lines[-1])
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out


# ---------------------------------------------------------------- Calendar: Plan blocks (v0.17, KAN-61)
# The only write into the calendar: create time blocks without invitees, and remove events of a batch the
# dashboard created itself (undo). Removal needs both the stored identifier and the tag in the notes.

PLAN_TAG = "Created by WorkDashboard (Plan blocks)"


def plan_calendar(store, cfg: dict, cal_id: str = ""):
    """The calendar asked for; else config planner.calendar; else the Exchange 'Calendar'; else any writable
    Exchange calendar; else the default calendar for new events."""
    EK, _ = _ek()
    if cal_id:
        cal = store.calendarWithIdentifier_(cal_id)
        if cal is None:
            raise ValueError("That calendar is no longer available")
        return cal
    cals = [k for k in store.calendarsForEntityType_(EK.EKEntityTypeEvent) if k.allowsContentModifications()]
    want = ((cfg.get("planner") or {}).get("calendar") or "").strip()
    exch = [k for k in cals if k.source() is not None and k.source().sourceType() == 1]
    for pick in ([k for k in cals if want and k.title() == want], [k for k in exch if k.title() == "Calendar"], exch):
        if pick:
            return pick[0]
    cal = store.defaultCalendarForNewEvents()
    if cal is None:
        raise ValueError("No writable calendar found")
    return cal


def create_events(store, req: dict) -> dict:
    """req: calendar_id, batch, blocks [{title, notes, start, end}] (local "YYYY-MM-DDTHH:MM"). Saved one by one, so a
    failure leaves the rows before it booked and is reported per row."""
    EK, _ = _ek()
    if not _request_access(store, "events"):
        raise PermissionError("Calendar access denied (System Settings > Privacy & Security > Calendars)")
    cal = plan_calendar(store, load_config(), req.get("calendar_id") or "")
    if not cal.allowsContentModifications():
        raise ValueError(f"Calendar '{cal.title()}' is read-only")
    created, errors = [], []
    for i, b in enumerate(req["blocks"]):
        try:
            ev = EK.EKEvent.eventWithEventStore_(store)
            ev.setTitle_(b["title"])
            ev.setStartDate_(_nsdate(datetime.strptime(b["start"], "%Y-%m-%dT%H:%M")))
            ev.setEndDate_(_nsdate(datetime.strptime(b["end"], "%Y-%m-%dT%H:%M")))
            ev.setCalendar_(cal)
            ev.setAvailability_(0)          # EKEventAvailabilityBusy
            tag = f"{PLAN_TAG}, batch {req.get('batch', '')}"     # last line, needed for undo
            ev.setNotes_(f"{b['notes']}\n\n{tag}" if b.get("notes") else tag)
            res = store.saveEvent_span_commit_error_(ev, 0, True, None)
            ok, err = res if isinstance(res, tuple) else (res, None)
            if not ok:
                raise RuntimeError(err.localizedDescription() if err is not None else "save failed")
            created.append({"i": i, "id": str(ev.eventIdentifier()), "title": b["title"], "start": b["start"], "end": b["end"]})
        except Exception as e:
            errors.append({"i": i, "error": f"{type(e).__name__}: {e}"})
    return {"calendar": cal.title(), "account": cal.source().title() if cal.source() is not None else "",
            "created": created, "errors": errors}


def remove_events(store, ids: list) -> dict:
    """Undo: remove only events that still carry PLAN_TAG. Anything else is left alone and reported."""
    if not _request_access(store, "events"):
        raise PermissionError("Calendar access denied (System Settings > Privacy & Security > Calendars)")
    removed, skipped = [], []
    for eid in ids:
        ev = store.eventWithIdentifier_(eid)
        if ev is None:
            skipped.append({"id": eid, "why": "already gone"})
            continue
        if PLAN_TAG not in (ev.notes() or ""):
            skipped.append({"id": eid, "why": "not created by the dashboard"})
            continue
        res = store.removeEvent_span_commit_error_(ev, 0, True, None)
        ok, err = res if isinstance(res, tuple) else (res, None)
        if ok:
            removed.append(eid)
        else:
            skipped.append({"id": eid, "why": err.localizedDescription() if err is not None else "remove failed"})
    return {"removed": removed, "skipped": skipped}


def _child(flag: str, payload, timeout: int = 90):
    """Run this file in child mode with JSON on stdin (same reason as eventkit_child). No retry."""
    r = subprocess.run([sys.executable, str(HERE / "harvest.py"), flag], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=timeout, cwd=HERE)
    lines = r.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(lines[-1])
    if isinstance(out, dict) and out.get("error"):
        raise RuntimeError(out["error"])
    return out


def add_events_child(req: dict) -> dict:
    return _child("--add-events", req)


def remove_events_child(ids: list) -> dict:
    return _child("--remove-events", ids)


# ---------------------------------------------------------------- EventKit in a fresh process
# Calendar access can turn "denied" inside the long-running service while the permission is still on
# (22 Oct, twice); only a restart cured it. So every harvest reads EventKit in a short-lived child
# process, which starts as clean as a restart, and falls back to this process only if that fails.

def harvest_eventkit(cfg: dict, now: datetime) -> dict:
    out = {"errors": {}}
    try:
        EK, _ = _ek()
        store = EK.EKEventStore.alloc().init()  # fresh store each run, so external edits are seen
    except Exception as e:
        out["errors"]["calendar"] = out["errors"]["reminders"] = f"EventKit unavailable: {e}"
        return out
    for name, fn in (("calendar", harvest_calendar), ("reminders", harvest_reminders)):
        try:
            out[name] = fn(store, cfg, now)
        except Exception as e:
            out["errors"][name] = f"{type(e).__name__}: {e}"
    return out


def eventkit_child(timeout: int = 150) -> dict:
    r = subprocess.run([sys.executable, str(HERE / "harvest.py"), "--eventkit"],
                       capture_output=True, text=True, timeout=timeout, cwd=HERE)
    lines = r.stdout.strip().splitlines()
    if r.returncode != 0 or not lines:
        raise RuntimeError(f"exit {r.returncode}: {r.stderr.strip()[-300:]}")
    return json.loads(lines[-1])


def read_eventkit(cfg: dict, now: datetime) -> tuple:
    """Child process first; in-process only if the child fails or does worse. Returns (result, how)."""
    ek, how = None, "child"
    try:
        ek = eventkit_child()
    except Exception as e:
        log(f"eventkit child failed: {type(e).__name__}: {e}")
    if ek is None or ek["errors"]:
        mine = harvest_eventkit(cfg, now)
        if ek is None or len(mine["errors"]) < len(ek["errors"]):
            ek, how = mine, "in-process"
    return ek, how


# ---------------------------------------------------------------- Obsidian

TASK_RE = re.compile(r"^\s*[-*]\s+\[ \]\s+(.+)$")
DUE_RE = re.compile(r"(?:📅|due::)\s*(\d{4}-\d{2}-\d{2})")
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
FM_DATE_RE = re.compile(r"^(?:date|due|deadline):\s*['\"]?(\d{4}-\d{2}-\d{2})", re.M | re.I)
FM_DUE_RE = re.compile(r"^(?:due|deadline):\s*['\"]?(\d{4}-\d{2}-\d{2})", re.M | re.I)   # deadline radar (v0.39)
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")   # Jira keys mentioned in notes (v0.39, KAN-79)
# explicit meeting links (v0.54, KAN-96)
LINK_RE = re.compile(r"\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
MKEY_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
MKEY_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")


def meeting_key(t) -> str:
    """Series key of a meeting title; same rule as mkey() in dashboard.html. A leading date is dropped, so
    'meeting: [[2026-11-05 Steering group]]' and 'meeting: Steering group' both match the event 'Steering group'."""
    import unicodedata
    s = re.sub(r"^\s*\[\[([^\]|]*)(?:\|[^\]]*)?\]\]\s*$", r"\1", str(t or ""))
    s = MKEY_DATE.sub("", s.strip())
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", MKEY_BAD.sub(" ", s)).strip().strip(".").strip()


def fm_values(fm: str, *keys) -> list:
    """Values of a frontmatter key: 'k: v', 'k: "v"', 'k: [a, b]' or a block list under 'k:'."""
    out = []
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(%s):\s*(.*)$" % "|".join(map(re.escape, keys)), line, re.I)
        if not m:
            continue
        v = m.group(2).strip()
        if v.startswith("[") and v.endswith("]") and not v.startswith("[["):
            items = [x.strip() for x in v[1:-1].split(",")]
        elif v:
            items = [v]
        else:
            items = []
            for nx in lines[i + 1:]:
                b = re.match(r"^\s*-\s+(.*)$", nx)
                if not b:
                    break
                items.append(b.group(1).strip())
        out += [x[1:-1] if len(x) > 1 and x[0] == x[-1] and x[0] in "'\"" else x for x in items]
    return [x for x in out if x]


def _excerpt(body: str, n: int = 180) -> str:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "- [", "* [", "![[", "```", "|", ">")) or re.fullmatch(r"[-*+]|\*\*\w+:\*\*.*", s):
            continue
        s = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), s)
        s = re.sub(r"[*_`]", "", s)
        out.append(s)
        if sum(len(x) for x in out) > n:
            break
    txt = " ".join(out)
    return txt[:n].rstrip() + ("…" if len(txt) > n else "")


def obsidian_scope(o: dict, vault: Path):
    """Yield every .md file selected by folders + files, minus exclude patterns."""
    excl = o.get("exclude", [])
    seen = set()

    def ok(p: Path) -> bool:
        rel = p.relative_to(vault).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            return False
        return not any(fnmatch.fnmatch(rel, pat) for pat in excl)

    for folder in o.get("folders", []):
        root = vault / folder
        if root.is_dir():
            for p in root.rglob("*.md"):
                if p not in seen and ok(p):
                    seen.add(p)
                    yield p
    for f in o.get("files", []):
        p = vault / (f if f.endswith(".md") else f + ".md")
        if p.is_file() and p not in seen and ok(p):
            seen.add(p)
            yield p


def obsidian_on(cfg: dict) -> bool:
    """v0.71: obsidian.enabled (default true) turns the Obsidian source off, like jira.enabled."""
    return bool(cfg.get("obsidian", {}).get("enabled", True))


def harvest_obsidian(cfg: dict, now: datetime) -> dict:
    if not obsidian_on(cfg):
        return None
    o = cfg["obsidian"]
    vault = Path(o["vault_path"]).expanduser()
    if not vault.exists():
        raise FileNotFoundError(f"Vault not found: {vault}")
    vault_name = o.get("vault_name") or vault.name
    tag = re.escape(o.get("pin_tag", "dashboard"))
    pin_re = re.compile(r"(^|\s)#%s\b|^%s:\s*true" % (tag, tag), re.M)
    c = cfg["calendar"]
    lo = now - timedelta(days=c["days_back"])
    hi = now + timedelta(days=c["days_ahead"] + 1)
    tasks, notes, refs = [], [], {}
    mtag, mlinks, by_stem = {}, {}, {}    # v0.54: meeting key -> notes tagged with meeting:, links in meeting notes

    for p in obsidian_scope(o, vault):
        try:
            st = p.stat()
            mtime = datetime.fromtimestamp(st.st_mtime)
            ctime = datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime))  # birth time on macOS
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue  # e.g. OneDrive placeholder not downloaded
        rel = p.relative_to(vault).as_posix()
        uri = "obsidian://open?" + urllib.parse.urlencode(
            {"vault": vault_name, "file": rel[:-3]}, quote_via=urllib.parse.quote)
        fm = FM_RE.match(text)
        body = text[fm.end():] if fm else text
        fdate = FM_DATE_RE.search(fm.group(1)) if fm else None
        dated = datetime.strptime(fdate.group(1), "%Y-%m-%d") if fdate else None
        pinned = bool(pin_re.search(text))
        fdue = FM_DUE_RE.search(fm.group(1)) if fm else None
        entry = {"title": p.stem, "folder": str(Path(rel).parent), "uri": uri, "modified": iso(mtime),
                 "snippet": _excerpt(body, 120)}
        by_stem.setdefault(p.stem.lower(), entry)
        if fm:
            for v in fm_values(fm.group(1), "meeting", "meetings"):
                k = meeting_key(v)
                if k:
                    mtag.setdefault(k, []).append(entry)
            ev = fm_values(fm.group(1), "event")
            if ev and [t.lower() for t in fm_values(fm.group(1), "type")] == ["meeting"] and meeting_key(ev[0]):
                lst = mlinks.setdefault(meeting_key(ev[0]), {"own": set(), "links": []})
                lst["own"].add(uri)
                lst["links"] += [m.strip() for m in LINK_RE.findall(body)]
        for k in dict.fromkeys(KEY_RE.findall(text)):
            lst = refs.setdefault(k, [])
            if len(lst) < 8:
                lst.append({"title": p.stem, "folder": str(Path(rel).parent), "uri": uri, "modified": iso(mtime)})
        if pinned or lo <= mtime <= hi or (dated and lo <= dated <= hi):
            notes.append({
                "title": p.stem, "folder": str(Path(rel).parent), "uri": uri,
                "modified": iso(mtime), "created": iso(min(ctime, mtime)), "date": iso(dated), "pinned": pinned,
                "due": fdue.group(1) if fdue else None,
                "excerpt": _excerpt(body),
            })
        for line in body.splitlines():
            m = TASK_RE.match(line)
            if not m:
                continue
            raw = m.group(1)
            d = DUE_RE.search(raw)
            due = datetime.strptime(d.group(1), "%Y-%m-%d") if d else None
            clean = re.sub(r"\s*(?:📅|due::)\s*\d{4}-\d{2}-\d{2}", "", raw).strip()
            tid = "obs:" + hashlib.sha1(f"{rel}|{clean}".encode()).hexdigest()[:12]
            tasks.append({"id": tid, "title": clean, "note": p.stem, "uri": uri, "due": iso(due), "_m": st.st_mtime})

    notes.sort(key=lambda n: n["modified"], reverse=True)
    meetings = meeting_links(mtag, mlinks, by_stem, vault_name)
    # undated tasks are capped; the most recently edited notes come first so a fresh task is never cut (v0.44)
    undated = sorted((t for t in tasks if not t["due"]), key=lambda t: -t["_m"])[: o.get("max_undated_tasks", 40)]
    for t in tasks:
        t.pop("_m", None)
    return {
        "tasks": [t for t in tasks if t["due"]] + undated,
        "notes": notes[: o.get("max_notes", 300)],
        "recent": [n for n in notes if n["modified"] >= iso(now - timedelta(days=o.get("recent_days", 7)))][:20],
        "pinned": [n for n in notes if n["pinned"]],
        "missing": [f for f in o.get("folders", []) if not (vault / f).is_dir()],
        "refs": {k: sorted(v, key=lambda n: n["modified"], reverse=True) for k, v in list(refs.items())[:2000]},
        "meetings": meetings,
    }


def meeting_links(mtag: dict, mlinks: dict, by_stem: dict, vault_name: str, cap: int = 20) -> dict:
    """v0.54 (KAN-96): meeting key -> linked notes. A note is linked when its frontmatter says meeting: <title>, or a
    meeting note of that series (type: meeting, event: <title>) links to it with [[...]]. A link to a note outside
    the dashboard folders still opens it by name. The series' own meeting notes are left out."""
    out = {}
    for k in set(mtag) | set(mlinks):
        own = mlinks.get(k, {}).get("own", set())
        rows, seen = [], set(own)
        for n in sorted(mtag.get(k, []), key=lambda n: n["modified"], reverse=True):
            if n["uri"] not in seen:
                seen.add(n["uri"])
                rows.append({**n, "how": "meeting"})
        for name in dict.fromkeys(mlinks.get(k, {}).get("links", [])):
            stem = name.rsplit("/", 1)[-1]
            if stem.lower().endswith(".md"):
                stem, name = stem[:-3], name[:-3]
            elif "." in stem and len(stem.rsplit(".", 1)[1]) <= 4 and stem.rsplit(".", 1)[1].isalpha():
                continue    # an attachment (pdf, png, ...), not a note
            n = by_stem.get(stem.lower()) or {
                "title": stem, "folder": "", "snippet": "",
                "uri": "obsidian://open?" + urllib.parse.urlencode({"vault": vault_name, "file": name},
                                                                   quote_via=urllib.parse.quote)}
            if n["uri"] not in seen:
                seen.add(n["uri"])
                rows.append({**n, "how": "link"})
        if rows:
            out[k] = rows[:cap]
    return dict(list(out.items())[:500])


def vault_tree(cfg: dict, max_depth: int = 3) -> dict:
    """Folder tree (with .md counts) for the settings panel."""
    o = cfg["obsidian"]
    vault = Path(o["vault_path"]).expanduser()
    counts = {}
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = Path(dirpath).relative_to(vault).as_posix()
        n = sum(1 for f in filenames if f.endswith(".md"))
        parts = [] if rel == "." else rel.split("/")
        # roll counts up into the ancestor shown at max_depth
        for k in range(1, min(len(parts), max_depth) + 1):
            key = "/".join(parts[:k])
            counts[key] = counts.get(key, 0) + n
    folders = [{"path": k, "depth": k.count("/"), "md": v} for k, v in sorted(counts.items(), key=lambda kv: kv[0].lower())]
    return {"vault": str(vault), "folders": folders,
            "selected": {"folders": o.get("folders", []), "files": o.get("files", []),
                         "exclude": o.get("exclude", [])}}


# ---------------------------------------------------------------- Jira (optional)

def _keychain(service: str, account: str) -> str:
    return subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True, check=True).stdout.strip()


JIRA_DEFAULT_JQL = ("project = KAN AND issuetype != Epic AND "
                    "(statusCategory != Done OR updated >= -14d) ORDER BY duedate ASC")


def jira_request(cfg: dict, method: str, path: str, body=None):
    """Authenticated call to the Jira Cloud REST API (token from macOS Keychain)."""
    j = cfg["jira"]
    token = _keychain(j["keychain_service"], j["email"])
    site = j["site"].rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{site}{path}", data=data, method=method)
    auth = base64.b64encode(f"{j['email']}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def harvest_jira(cfg: dict):
    j = cfg.get("jira", {})
    if not j.get("enabled"):
        return None
    site = j["site"].rstrip("/")
    jql = j.get("jql") or JIRA_DEFAULT_JQL.replace("project = KAN", f'project = "{j.get("project") or "KAN"}"')
    q = urllib.parse.urlencode({"jql": jql, "maxResults": 100,
                                "fields": "summary,status,duedate,priority,project,parent,updated,resolutiondate,labels,description"})
    payload = jira_request(cfg, "GET", f"/rest/api/3/search/jql?{q}")
    issues = []
    for it in payload.get("issues", []):
        f = it["fields"]
        due = datetime.strptime(f["duedate"], "%Y-%m-%d") if f.get("duedate") else None
        parent = f.get("parent") or {}
        labels = [str(x) for x in (f.get("labels") or [])]
        issues.append({
            "key": it["key"],
            "title": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "category": ((f.get("status") or {}).get("statusCategory") or {}).get("key", ""),
            "project": (f.get("project") or {}).get("key", ""),
            "epic": (parent.get("fields") or {}).get("summary", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "updated": (f.get("updated") or "")[:16],
            "resolved": (f.get("resolutiondate") or "")[:16] or None,
            "due": iso(due),
            "url": f"{site}/browse/{it['key']}",
            "labels": labels,
            # v0.50: on hold by a label (hold, onhold, on-hold) or #hold/@hold in the description
            "hold": any(x.lower() in HOLD_LABELS for x in labels) or is_on_hold(_adf_text(f.get("description"))),
            "hold_label": any(x.lower() in HOLD_LABELS for x in labels),
        })
    return {"issues": issues}


# ---------------------------------------------------------------- Jira: labels (v0.50, KAN-91)
# The one edit the dashboard makes to an existing issue: adding or removing its own labels, `hold` and
# `delegated`. Nothing else of the issue changes; the description is only read.

HOLD_LABELS = {"hold", "onhold", "on-hold"}
DELEG_LABEL = "delegated"


def _adf_text(node) -> str:
    """Plain text of an Atlassian Document Format description (text nodes joined by spaces)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return " ".join(x for x in ([node.get("text") or ""] + [_adf_text(c) for c in node.get("content") or []]) if x)
    if isinstance(node, list):
        return " ".join(_adf_text(c) for c in node)
    return ""


def jira_labels(cfg: dict, key: str, add=(), remove=()) -> list:
    """Add and remove labels (case-insensitive match for removal); one PUT, only when something changes."""
    cur = jira_request(cfg, "GET", f"/rest/api/3/issue/{key}?fields=labels")["fields"].get("labels") or []
    low = {x.lower() for x in cur}
    ops = [{"add": a} for a in add if a.lower() not in low]
    ops += [{"remove": x} for x in cur if x.lower() in {r.lower() for r in remove}]
    if ops:
        jira_request(cfg, "PUT", f"/rest/api/3/issue/{key}", {"update": {"labels": ops}})
    return [x for x in cur if x.lower() not in {r.lower() for r in remove}] + [o["add"] for o in ops if "add" in o]


def jira_move(cfg: dict, key: str, category: str, prefer: str = "") -> str:
    """Transition an issue into a status of the wanted category (new / indeterminate / done).
    `prefer` picks a status by name within that category (e.g. "Backlog" vs "To Do");
    a tuple gives accepted spellings (v0.49: "Delegated" / "Delagated")."""
    cur = jira_request(cfg, "GET", f"/rest/api/3/issue/{key}?fields=status")["fields"]["status"]
    norm = lambda s: s.replace(" ", "").lower()
    names = {norm(x) for x in ((prefer,) if isinstance(prefer, str) else prefer) if x}
    if cur["statusCategory"]["key"] == category and (not names or norm(cur["name"]) in names):
        return cur["name"]
    trs = [t for t in jira_request(cfg, "GET", f"/rest/api/3/issue/{key}/transitions").get("transitions", [])
           if t.get("to", {}).get("statusCategory", {}).get("key") == category]
    pick = next((t for t in trs if names and norm(t["to"]["name"]) in names), None)
    if pick is None and not isinstance(prefer, str):
        raise ValueError(f"No Jira transition from {key} to {' / '.join(prefer)}")
    if pick is None:
        if cur["statusCategory"]["key"] == category:
            return cur["name"]          # no status with that name; stay put
        pick = trs[0] if trs else None
    if pick is None:
        raise ValueError(f"No Jira transition from {key} to a '{category}' status")
    jira_request(cfg, "POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": pick["id"]}})
    return pick["to"]["name"]


# ---------------------------------------------------------------- Jira: create and log work (v0.42, KAN-71, KAN-82)
# Create-only: a new issue in the configured project, and worklogs on an issue. Nothing here edits or deletes.

def _adf(text: str) -> dict:
    paras = [p for p in str(text or "").split("\n\n") if p.strip()] or [""]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line} for line in [p.strip()] if line]} for p in paras]}


def jira_epics(cfg: dict) -> list:
    proj = cfg["jira"].get("project") or "KAN"
    q = urllib.parse.urlencode({"jql": f'project = "{proj}" AND issuetype = Epic AND statusCategory != Done ORDER BY summary ASC',
                                "maxResults": 100, "fields": "summary"})
    data = jira_request(cfg, "GET", f"/rest/api/3/search/jql?{q}")
    return [{"key": it["key"], "name": it["fields"].get("summary", "")} for it in data.get("issues", [])]


def jira_create(cfg: dict, item: dict) -> dict:
    j = cfg["jira"]
    fields = {"project": {"key": j.get("project") or "KAN"}, "summary": item["summary"],
              "issuetype": {"name": j.get("issue_type") or "Task"}}
    if item.get("epic"):
        fields["parent"] = {"key": item["epic"]}
    if item.get("due"):
        fields["duedate"] = item["due"]
    if item.get("description"):
        fields["description"] = _adf(item["description"])
    if item.get("priority"):
        fields["priority"] = {"name": item["priority"]}
    try:
        out = jira_request(cfg, "POST", "/rest/api/3/issue", {"fields": fields})
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:400]
        if "priority" in fields and "priority" in body:   # priority not on the create screen: try once without it
            fields.pop("priority")
            out = jira_request(cfg, "POST", "/rest/api/3/issue", {"fields": fields})
        else:
            raise ValueError(f"Jira refused the issue: {body}") from e
    return {"key": out["key"], "url": f"{j['site'].rstrip('/')}/browse/{out['key']}"}


def jira_worklog(cfg: dict, key: str, started: datetime, seconds: int, comment: str = "") -> dict:
    body = {"timeSpentSeconds": int(seconds),
            "started": started.astimezone().strftime("%Y-%m-%dT%H:%M:%S.000%z")}
    if comment.strip():
        body["comment"] = _adf(comment)
    try:
        out = jira_request(cfg, "POST", f"/rest/api/3/issue/{key}/worklog", body)
    except urllib.error.HTTPError as e:
        raise ValueError(f"Jira refused the worklog: {e.read().decode(errors='ignore')[:300]}") from e
    return {"id": out.get("id"), "key": key, "seconds": int(seconds)}


# ---------------------------------------------------------------- main

HINTS = [  # (source or "*", lower-case text in the error, hint shown in the health panel)
    ("calendar", "denied", "Allow WorkDashboard in System Settings > Privacy & Security > Calendars (full access), "
                           "then press Refresh."),
    ("reminders", "denied", "Allow WorkDashboard in System Settings > Privacy & Security > Reminders, then press Refresh."),
    ("*", "eventkit unavailable", "pyobjc is missing in the dashboard's Python. Run install.sh again."),
    ("obsidian", "vault not found", "The vault folder is missing. Is the cloud drive that holds it (iCloud, OneDrive, Dropbox) signed in and the vault synced? "
                                    "Check obsidian.vault_path in config.json."),
    ("jira", "could not be found in the keychain", "No Jira token in Keychain. Create an API token at "
                                                   "id.atlassian.com and store it as service 'work-dashboard-jira'."),
    ("jira", "calledprocesserror", "No Jira token in Keychain. Create an API token at id.atlassian.com and store it "
                                   "as service 'work-dashboard-jira'."),
    ("jira", "401", "Jira rejected the token (expired or revoked). Create a new API token and replace it in Keychain."),
    ("jira", "403", "Jira refused access with this token. Check the account and its permissions."),
    ("jira", "urlerror", "Cannot reach Jira. Check the network; the dashboard keeps the last issues it had."),
    ("jira", "timed out", "Jira did not answer in time; the dashboard keeps the last issues it had."),
]


def hint_for(name: str, err: str) -> str:
    e = (err or "").lower()
    return next((h for src, k, h in HINTS if src in (name, "*") and k in e), "")


def _count(name: str, block) -> int:
    if not isinstance(block, dict):
        return 0
    return len(block.get({"calendar": "events", "reminders": "open", "obsidian": "notes", "jira": "issues"}[name]) or [])


def harvest(enrich=None) -> dict:
    """enrich(data, cfg): optional step before the snapshot is written (v0.70: Outlook category colours)."""
    cfg = load_config()
    now = datetime.now()
    data = {"generated": iso(now), "synced": {}, "errors": {}, "user_name": cfg.get("user_name", ""),
            "refresh_minutes": cfg.get("refresh_minutes", 5)}
    ms = {}
    day0 = datetime.combine(now.date(), datetime.min.time())
    data["window"] = {"from": iso(day0 - timedelta(days=cfg["calendar"]["days_back"])),
                      "to": iso(day0 + timedelta(days=cfg["calendar"]["days_ahead"]))}

    def run(name, fn):
        t0 = time.monotonic()
        try:
            result = fn()
            if result is not None:
                data[name] = result
                data["synced"][name] = iso(datetime.now())
        except Exception as e:  # keep other sources alive
            data["errors"][name] = f"{type(e).__name__}: {e}"
        ms[name] = int((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    ek, how = read_eventkit(cfg, now)
    ms["calendar"] = ms["reminders"] = int((time.monotonic() - t0) * 1000)
    for name in ("calendar", "reminders"):
        if name in ek:
            data[name] = ek[name]
            data["synced"][name] = iso(datetime.now())
        if name in ek["errors"]:
            data["errors"][name] = ek["errors"][name]
    if ek["errors"]:
        log(f"eventkit ({how}): {ek['errors']}")
    run("obsidian", lambda: harvest_obsidian(cfg, now))
    run("jira", lambda: harvest_jira(cfg))

    out = Path(cfg["output_dir"]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    try:
        prev = json.loads((out / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if enrich and "calendar" in data:
        try:
            enrich(data, cfg)
        except Exception as e:  # colours are a nicety, never block the snapshot
            log(f"enrich failed: {type(e).__name__}: {e}")
    data["health"] = health(cfg, data, prev, ms)
    tmp = out / "data.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / "data.json")  # atomic, the page never reads half a file
    return data


def obsidian_signature(cfg: dict) -> tuple:
    """Cheap fingerprint of the Obsidian scope (path, mtime, size per file): changes when a note is
    edited, added, renamed or removed. Only stat() calls, no file is read (v0.44)."""
    if not obsidian_on(cfg):
        return ()
    o = cfg["obsidian"]
    vault = Path(o["vault_path"]).expanduser()
    out = []
    if vault.exists():
        for p in obsidian_scope(o, vault):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((str(p), st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


def refresh_obsidian() -> dict:
    """Re-read only the Obsidian part and patch it into the last snapshot (v0.44): hand edits in the
    vault show without waiting for the full harvest. Calendar, Reminders and Jira are left as they are."""
    cfg = load_config()
    now = datetime.now()
    out = Path(cfg["output_dir"]).expanduser()
    try:
        data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return harvest()
    t0 = time.monotonic()
    data.setdefault("synced", {})
    data.setdefault("errors", {})
    try:
        data["obsidian"] = harvest_obsidian(cfg, now)
        data["synced"]["obsidian"] = iso(datetime.now())
        data["errors"].pop("obsidian", None)
    except Exception as e:  # keep the previous Obsidian data
        data["errors"]["obsidian"] = f"{type(e).__name__}: {e}"
    ms = {k: (v or {}).get("ms") for k, v in (data.get("health") or {}).items()}
    ms["obsidian"] = int((time.monotonic() - t0) * 1000)
    data["health"] = {**(data.get("health") or {}), "obsidian": health(cfg, data, data, ms)["obsidian"]}
    tmp = out / "data.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / "data.json")
    return data


def health(cfg: dict, data: dict, prev: dict, ms: dict) -> dict:
    """Per source: state ok / warn / error / off, last good sync, item count, harvest time, error and hint.
    A source that fails keeps the data of the previous snapshot (marked carried), so one broken source
    does not empty its part of the page; the page shows how old that data is."""
    out = {}
    off = {"jira": not cfg.get("jira", {}).get("enabled"), "obsidian": not obsidian_on(cfg)}
    for name in ("calendar", "reminders", "obsidian", "jira"):
        err = data["errors"].get(name, "")
        h = {"state": "ok", "synced": data["synced"].get(name), "count": _count(name, data.get(name)),
             "ms": ms.get(name), "error": err, "hint": hint_for(name, err) if err else "", "carried": False}
        if off.get(name):
            h.update(state="off", synced=None)
        elif err:
            h["state"] = "error"
            old = prev.get(name)
            if old and name not in data:
                data[name] = old
                h.update(carried=True, count=_count(name, old),
                         synced=((prev.get("health") or {}).get(name) or {}).get("synced") or prev.get("synced", {}).get(name))
        if name == "obsidian" and not err and (data.get("obsidian") or {}).get("missing"):
            h.update(state="warn", error="Folders not found: " + ", ".join(data["obsidian"]["missing"]),
                     hint="Pick the folders again under Settings > Data > Obsidian sources, or restore them in the vault.")
        out[name] = h
    return out


if __name__ == "__main__":
    if "--eventkit" in sys.argv:  # child mode, see read_eventkit()
        print(json.dumps(harvest_eventkit(load_config(), datetime.now())))
        sys.exit(0)
    if "--period-events" in sys.argv:  # child mode, see period_events_child()
        try:
            i = sys.argv.index("--period-events")
            a, b = (datetime.strptime(x, "%Y-%m-%d") for x in sys.argv[i + 1:i + 3])
            EK, _ = _ek()
            print(json.dumps(period_events(EK.EKEventStore.alloc().init(), load_config(), a, b + timedelta(days=1))))
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(0)
    if "--add-reminder" in sys.argv:  # child mode, see add_reminder_child()
        try:
            EK, _ = _ek()
            print(json.dumps(create_reminder(EK.EKEventStore.alloc().init(), json.loads(sys.stdin.read()))))
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(0)
    for flag, fn in (("--add-events", create_events), ("--remove-events", remove_events), ("--add-reminders", create_reminders),
                     ("--remove-reminders", remove_reminders), ("--set-hold", set_hold)):
        if flag in sys.argv:  # child mode, see _child()
            try:
                EK, _ = _ek()
                print(json.dumps(fn(EK.EKEventStore.alloc().init(), json.loads(sys.stdin.read()))))
            except Exception as e:
                print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
            sys.exit(0)
    d = harvest()
    counts = {
        "events": len(d.get("calendar", {}).get("events", [])),
        "reminders": len(d.get("reminders", {}).get("open", [])),
        "obsidian_tasks": len(d.get("obsidian", {}).get("tasks", [])),
        "jira": len((d.get("jira") or {}).get("issues", [])),
    }
    print("Harvested:", counts)
    for k, v in d["errors"].items():
        print(f"  {k}: {v}")
