"""Dummy data for the demo server and the README screenshots. Nothing here is real: every title, person,
note and Jira key is made up. Dates are relative to today, so the page always looks current.

build(now) returns (data, period_events):
- data: the same shape as harvest.py's data.json (calendar, reminders, obsidian, jira);
- period_events: the longer event history stats.py, ahead.py and planner.py read.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

SEED = 7

# (title, calendar, minutes, weight); titles hit the example rules in config.example.json -> stats
MEETINGS = [
    ("Project sync: Surface Lab", "Work", 60, 6),
    ("Project steering group", "Work", 90, 2),
    ("Lecture: Manufacturing Metrology", "Courses", 90, 5),
    ("Course lab session", "Courses", 120, 2),
    ("PhD supervision: Maja", "Work", 60, 4),
    ("Thesis meeting: exjobb group", "Work", 45, 3),
    ("Paper writing: roughness review", "Work", 120, 3),
    ("Review for journal submission", "Work", 90, 2),
    ("Proposal draft: Vinnova call", "Work", 120, 2),
    ("Department meeting", "Work", 60, 2),
    ("Measurement campaign in the lab", "Work", 180, 2),
    ("Research seminar", "Work", 60, 2),
    ("Deep work", "Work", 120, 4),
    ("Fika with the group", "Work", 30, 2),
    ("Interview: research engineer", "Work", 45, 1),
    ("Webinar: AI in quality control", "Work", 60, 1),
]
SLOTS = [8 * 60 + 30, 9 * 60, 10 * 60, 10 * 60 + 30, 13 * 60, 13 * 60 + 30, 14 * 60 + 30, 15 * 60, 16 * 60]

CALENDARS = [
    {"name": "Work", "color": "#3b82f6", "id": "demo-work", "account": "Exchange", "exchange": True, "writable": True},
    {"name": "Courses", "color": "#a855f7", "id": "demo-courses", "account": "Exchange", "exchange": True, "writable": True},
    {"name": "Home", "color": "#22c55e", "id": "demo-home", "account": "iCloud", "exchange": False, "writable": True},
    {"name": "Holidays", "color": "#f59e0b", "id": "demo-holidays", "account": "Subscribed", "exchange": False, "writable": False},
]


# v0.70 (KAN-110): made-up Outlook categories with colours; Exchange events matching a keyword carry one ("ocat"),
# so the demo shows category colours. Review is pale, so its event text turns dark.
OUTLOOK_CATEGORIES = [
    {"name": "Teaching", "color": "#e8710a"}, {"name": "Research", "color": "#2e9e5b"},
    {"name": "Focus Work", "color": "#0f6c8f"}, {"name": "Admin", "color": "#7a7f87"},
    {"name": "Review", "color": "#f6e08a"}, {"name": "Projects", "color": "#c2185b"},
]
OCAT_RULES = [("lecture", "Teaching"), ("course", "Teaching"), ("supervision", "Teaching"), ("thesis", "Teaching"),
              ("paper", "Research"), ("measurement", "Research"), ("seminar", "Research"), ("deep work", "Focus Work"),
              ("review", "Review"), ("project", "Projects"), ("proposal", "Projects"), ("department", "Admin"),
              ("interview", "Admin")]


def _ocat(title: str, cal: str) -> list:
    if cal not in ("Work", "Courses"):
        return []
    t = title.lower()
    return next(([c] for k, c in OCAT_RULES if k in t), [])


def iso(d) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M") if d else None


def _day_events(rng: random.Random, day: date, now: datetime) -> list:
    if day.weekday() >= 5:
        if rng.random() < 0.25:
            t = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
            return [("Family lunch", "Home", t, t + timedelta(hours=2), False, "busy")]
        return []
    # minutes already taken; today keeps 14:00 to 15:30 for the fixed partner review in _events()
    used = [(14 * 60, 15 * 60 + 30)] if day == now.date() else []
    out = []
    # further ahead = less booked yet
    lead = (day - now.date()).days
    n = rng.randint(2, 5) if lead < 7 else rng.randint(1, 3) if lead < 21 else rng.randint(0, 2)
    for _ in range(n):
        title, cal, mins, _w = rng.choices(MEETINGS, weights=[m[3] for m in MEETINGS])[0]
        slot = rng.choice(SLOTS)
        if any(slot < b and a < slot + mins for a, b in used) or slot + mins > 18 * 60:
            continue
        used.append((slot, slot + mins))
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=slot)
        out.append((title, cal, start, start + timedelta(minutes=mins), False, "busy"))
    if rng.random() < 0.5:
        start = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)
        out.append(("Lunch", "Home", start, start + timedelta(minutes=45), False, "free"))
    return out


def _events(now: datetime, first: date, last: date) -> list:
    rng = random.Random(SEED)
    out, d = [], first
    while d <= last:
        out += _day_events(rng, d, now)
        d += timedelta(days=1)
    # a few fixed highlights around today
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    def at(day, h, m=0):
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)
    conf = monday + timedelta(weeks=2, days=1)
    out += [
        ("Conference: Surface Metrology Summit", "Work", at(conf, 0), at(conf + timedelta(days=3), 0), True, "busy"),
        ("Train to Gothenburg", "Work", at(conf, 7), at(conf, 10, 15), False, "busy"),
        ("Vacation", "Home", at(monday + timedelta(weeks=7), 0), at(monday + timedelta(weeks=7, days=5), 0), True, "free"),
        ("All Saints' Day", "Holidays", at(today + timedelta(days=(5 - today.weekday()) % 7 or 7), 0),
         at(today + timedelta(days=(5 - today.weekday()) % 7 or 7, hours=24), 0), True, "free"),
        ("Project review with partners", "Work", at(today, 14), at(today, 15, 30), False, "busy"),
        ("Call with the journal editor", "Work", at(today, 15, 15), at(today, 15, 45), False, "busy"),
        ("Dentist", "Home", at(today + timedelta(days=1), 16, 30), at(today + timedelta(days=1), 17, 15), False, "busy"),
    ]
    out.sort(key=lambda e: e[2])
    return out


def build(now: datetime | None = None):
    now = now or datetime.now()
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    win_from, win_to = today - timedelta(days=35), today + timedelta(days=70)
    raw = _events(now, monday - timedelta(weeks=110), today + timedelta(days=70))
    rng = random.Random(SEED + 1)

    period = []
    for i, (title, cal, s, e, all_day, avail) in enumerate(raw):
        lead = int(rng.expovariate(1 / 12)) + 1           # days between booking and the event
        created = s - timedelta(days=lead)
        if created > now:
            continue                                     # not booked yet
        period.append({"id": f"demo-{i}|{iso(s)}", "title": title, "start": iso(s), "end": iso(e),
                       "all_day": all_day, "avail": avail, "created": iso(created)})
    booked = {p["id"].split("|")[0] for p in period}

    events = [{"title": t, "start": iso(s), "end": iso(e), "all_day": a, "calendar": c,
               "location": "Room M311" if "Lecture" in t else "Zoom" if "sync" in t.lower() else "",
               "free": av == "free", **({"ocat": _ocat(t, c)} if _ocat(t, c) else {})}
              for i, (t, c, s, e, a, av) in enumerate(raw)
              if f"demo-{i}" in booked and win_from <= s.date() <= win_to]
    # event detail panel (v0.36): an id per event, and full details on a few of today's events
    people = [("Anna Lindqvist", "accepted"), ("Marco Rossi", "accepted"), ("Jonas Berg", "tentative"),
              ("Sara Holm", "pending"), ("Li Wei", "accepted"), ("Erik Nyström", "declined"), ("Maria Kowalska", "accepted")]
    att = lambda n: [{"name": a, "email": a.split()[0].lower() + "@example.org", "me": False, "status": st, "role": "required"}
                     for a, st in people[:n]] + [{"name": "You", "email": "you@example.org", "me": True, "status": "accepted", "role": "chair"}]
    for k, ev in enumerate(events):
        ev["id"] = f"demo{k:05d}"
        if ev["title"] == "Project review with partners" and ev["start"][:10] == today.isoformat():
            ev.update(location="Room M311", url="", organizer={"name": "You", "email": "you@example.org", "me": True},
                      attendees=att(7), attendees_total=8, repeat={"freq": "weekly", "every": 2},
                      notes="Agenda\n1. Status of the measurement campaign (DEMO-12)\n2. Paper revision plan, see DEMO-21\n"
                            "3. Next steps and dates\n\nNotes from last time: [[Meeting notes: project steering group]]\n\n"
                            "________________________________________________________________________________\n"
                            "Microsoft Teams meeting\nJoin: https://teams.microsoft.com/l/meetup-join/19%3ameeting_DEMO%40thread.v2/0?context=demo\n"
                            "Meeting ID: 000 000 000 000\n" + "Passcode: demo\n" * 1 +
                            "\nFor organizers: Meeting options\n________________________________________________________________________________")
        elif ev["title"] == "Call with the journal editor" and ev["start"][:10] == today.isoformat():
            ev.update(location="https://example.zoom.us/j/1234567890", url="",
                      organizer={"name": "Dr. P. Editor", "email": "editor@example.org", "me": False},
                      attendees=att(1), attendees_total=2, notes="Discuss the revised manuscript and the reviewer 2 comments.")

    def due(days, h=None):
        d = datetime.combine(today + timedelta(days=days), datetime.min.time())
        return iso(d + timedelta(hours=h) if h is not None else d)

    reminders = {
        "open": [
            {"id": "rem:d1", "title": "Send slides to course participants", "list": "Work", "due": due(-2), "has_time": False, "priority": 1, "notes": ""},
            {"id": "rem:d2", "title": "Book room for thesis defence", "list": "Work", "due": due(-1, 10), "has_time": True, "priority": 5, "notes": ""},
            {"id": "rem:d3", "title": "Approve travel expenses", "list": "Admin", "due": due(0, 11), "has_time": True, "priority": 1, "notes": ""},
            {"id": "rem:d4", "title": "Reply to reviewer comments", "list": "Work", "due": due(0), "has_time": False, "priority": 5, "notes": "Round 2"},
            {"id": "rem:d5", "title": "Order calibration standards", "list": "Work", "due": due(1, 9), "has_time": True, "priority": 0, "notes": ""},
            {"id": "rem:d6", "title": "Prepare exam questions", "list": "Work", "due": due(3), "has_time": False, "priority": 5, "notes": ""},
            {"id": "rem:d7", "title": "Renew parking permit", "list": "Personal", "due": due(4), "has_time": False, "priority": 0, "notes": ""},
            {"id": "rem:d8", "title": "Update group web page", "list": "Admin", "due": due(9), "has_time": False, "priority": 9, "notes": ""},
            {"id": "rem:d9", "title": "Submit conference abstract", "list": "Work", "due": due(12), "has_time": False, "priority": 1, "notes": ""},
            {"id": "rem:d10", "title": "Read the new ISO 25178 draft", "list": "Work", "due": None, "has_time": False, "priority": 0, "notes": ""},
            {"id": "rem:d11", "title": "Plan group kick-off", "list": "Personal", "due": None, "has_time": False, "priority": 0, "notes": ""},
            # v0.47: on hold by a mark in the notes
            {"id": "rem:d12", "title": "Order replacement stylus", "list": "Work", "due": due(-4), "has_time": False, "priority": 5,
             "notes": "Waiting for the supplier quote\n\n#hold", "hold": True},
        ],
        "done_7d": 6,
        "completed": [
            {"id": "rem:c1", "title": "Sign student contracts", "list": "Admin", "completed": due(-1, 15)},
            {"id": "rem:c2", "title": "Send budget to finance", "list": "Work", "completed": due(-3, 10)},
            {"id": "rem:c3", "title": "Return borrowed gauge blocks", "list": "Work", "completed": due(-8, 14)},
            {"id": "rem:c4", "title": "Pay conference fee", "list": "Admin", "completed": due(-10, 9)},
        ],
        "lists": [{"name": "Work", "color": "#3b82f6", "writable": True},
                  {"name": "Admin", "color": "#f97316", "writable": True},
                  {"name": "Personal", "color": "#22c55e", "writable": True}],
        "default_list": "Work",
    }

    def uri(name):
        return "obsidian://open?vault=Demo&file=" + name.replace(" ", "%20")

    notes = [
        {"title": "Roughness parameters cheat sheet", "folder": "Learning", "pinned": True, "days": -20, "date": None,
         "excerpt": "Sa, Sq, Sz, Ssk, Sku: what each one tells you, typical ranges for ground and turned surfaces.", "age": 60},
        {"title": "Weekly plan", "folder": "Inbox", "pinned": True, "days": 0, "date": 0,
         "excerpt": "Focus: paper revision, lab campaign prep, course week 5.", "age": 30},
        {"title": "Meeting notes: project steering group", "folder": "Inbox", "pinned": False, "days": -1, "date": -1,
         "excerpt": "Decisions: extend the measurement campaign by two weeks; partners send samples by Friday."},
        {"title": "Idea: ML for defect detection", "folder": "Research/NOTES", "pinned": False, "days": -3, "date": None,
         "excerpt": "Train a small classifier on areal topography maps; start with the 2025 data set."},
        {"title": "Literature: laser texturing", "folder": "Research/NOTES", "pinned": False, "days": -5, "date": None,
         "excerpt": "Five key papers on dimple patterns and friction; summary table in progress."},
    ]
    obsidian = {
        "tasks": [
            {"id": "obs:a1", "title": "Draft figure 4 for the paper", "note": "Weekly plan", "uri": uri("Inbox/Weekly plan"), "due": due(1)},
            {"id": "obs:a2", "title": "Check sample IDs before the lab day", "note": "Meeting notes: project steering group", "uri": uri("Inbox/Meeting notes"), "due": due(2)},
            {"id": "obs:a3", "title": "Write related-work section", "note": "Literature: laser texturing", "uri": uri("Research/NOTES/Literature"), "due": due(6)},
            {"id": "obs:a4", "title": "Collect training images", "note": "Idea: ML for defect detection", "uri": uri("Research/NOTES/Idea"), "due": None},
        ],
        "notes": [],
    }
    for n in notes:
        m = datetime.combine(today + timedelta(days=n["days"]), datetime.min.time()) + timedelta(hours=9)
        obsidian["notes"].append({"title": n["title"], "folder": n["folder"], "uri": uri(n["folder"] + "/" + n["title"]),
                                  "modified": iso(m), "created": iso(m - timedelta(days=n.get("age", 0))),
                                  "date": due(n["date"]) if n["date"] is not None else None,
                                  "pinned": n["pinned"], "excerpt": n["excerpt"]})
    obsidian["recent"] = [n for n in obsidian["notes"] if n["modified"] >= iso(now - timedelta(days=7))]
    obsidian["pinned"] = [n for n in obsidian["notes"] if n["pinned"]]
    obsidian["missing"] = []
    obsidian["notes"].append({"title": "Vinnova proposal plan", "folder": "Research/NOTES", "uri": uri("Research/NOTES/Vinnova proposal plan"),
                              "modified": iso(now - timedelta(days=2)), "created": iso(now - timedelta(days=9)),
                              "date": due(9), "due": due(9)[:10], "pinned": False,
                              "excerpt": "Work packages, budget and partner letters; internal deadline a week before the call closes."})
    for back, title, ex in ((7, "Project sync Surface Lab", "Agreed: two extra ground samples; Maja checks the confocal settings."),
                            (14, "Project sync Surface Lab", "First results look consistent; next step is the waviness filter.")):
        d0 = today - timedelta(days=back)
        name = f"{d0.isoformat()} {title}"
        obsidian["notes"].append({"title": name, "folder": "Research/NOTES/Meetings", "uri": uri("Research/NOTES/Meetings/" + name),
                                  "modified": iso(datetime.combine(d0, datetime.min.time()) + timedelta(hours=11)),
                                  "created": iso(datetime.combine(d0, datetime.min.time()) + timedelta(hours=9)),
                                  "date": iso(datetime.combine(d0, datetime.min.time())), "pinned": False, "excerpt": ex})
    for age, title, ex in ((23, "Paper idea: wear of textured surfaces", "Could the dimple density explain the lower wear rate?"),
                           (41, "Link to check later", "Webinar recording on in-situ roughness measurement.")):
        t0 = now - timedelta(days=age)
        obsidian["notes"].append({"title": title, "folder": "Inbox", "uri": uri("Inbox/" + title), "modified": iso(t0),
                                  "created": iso(t0), "date": None, "pinned": False, "excerpt": ex})
    obsidian["refs"] = {"DEMO-21": [{"title": "Meeting notes: project steering group", "folder": "Inbox", "uri": uri("Inbox/Meeting notes"),
                                     "modified": iso(now - timedelta(days=1))},
                                    {"title": "Literature: laser texturing", "folder": "Research/NOTES", "uri": uri("Research/NOTES/Literature"),
                                     "modified": iso(now - timedelta(days=5))}],
                        "DEMO-12": [{"title": "Weekly plan", "folder": "Inbox", "uri": uri("Inbox/Weekly plan"), "modified": iso(now)}]}
    # v0.54 (KAN-96): notes linked to a meeting series (meeting: in a note's properties, or a link in the meeting note)
    obsidian["meetings"] = {"project sync surface lab": [
        {"title": "Literature: laser texturing", "folder": "Research/NOTES", "uri": uri("Research/NOTES/Literature"),
         "modified": iso(now - timedelta(days=5)), "snippet": "Five key papers on dimple patterns and friction.", "how": "meeting"},
        {"title": "Idea: ML for defect detection", "folder": "Research/NOTES", "uri": uri("Research/NOTES/Idea"),
         "modified": iso(now - timedelta(days=3)), "snippet": "Train a small classifier on areal topography maps.", "how": "link"}]}

    def issue(key, title, status, cat, epic, prio, d=None, done_ago=None):
        upd = now - timedelta(hours=done_ago if done_ago is not None else rng.randint(1, 72))
        return {"key": key, "title": title, "status": status, "category": cat, "project": "DEMO", "epic": epic,
                "priority": prio, "updated": iso(upd), "resolved": iso(upd) if cat == "done" else None,
                "due": due(d) if d is not None else None, "url": "https://example.atlassian.net/browse/" + key}
    jira = {"issues": [
        issue("DEMO-12", "Calibrate the new confocal microscope", "In Progress", "indeterminate", "Lab upgrade", "High", 3),
        issue("DEMO-14", "Write measurement procedure", "To Do", "new", "Lab upgrade", "Medium", 8),
        dict(issue("DEMO-15", "Order spare objectives", "Backlog", "new", "Lab upgrade", "Low"), labels=["hold"], hold=True, hold_label=True),  # v0.50
        issue("DEMO-21", "Revise paper after review", "In Progress", "indeterminate", "Publications", "High", 5),
        issue("DEMO-22", "Prepare conference poster", "To Do", "new", "Publications", "Medium", 14),
        issue("DEMO-30", "Course week 5 material", "Done", "done", "Teaching", "Medium", -2, done_ago=26),
        issue("DEMO-24", "Submit revised abstract", "Done", "done", "Publications", "Medium", -6, done_ago=150),
        issue("DEMO-31", "Exam grading rubric", "Backlog", "new", "Teaching", "Low", 20),
        issue("DEMO-40", "Dashboard: export weekly report", "To Do", "new", "Work Dashboard", "Medium"),
        issue("DEMO-44", "Check measurement data from the lab", "Delegated", "new", "Lab upgrade", "Medium", -1),
        issue("DEMO-45", "Book the meeting room for the workshop", "Delegated", "new", "Teaching", "Low", 0),
        issue("DEMO-46", "Collect partner reports", "Delegated", "new", "Publications", "Medium"),
    ]}

    data = {
        "generated": iso(now), "synced": {k: iso(now) for k in ("calendar", "reminders", "obsidian", "jira")},
        "errors": {}, "user_name": "Alex", "refresh_minutes": 5,
        "window": {"from": iso(datetime.combine(win_from, datetime.min.time())),
                   "to": iso(datetime.combine(win_to, datetime.min.time()))},
        "calendar": {"events": events, "calendars": CALENDARS, "categories": OUTLOOK_CATEGORIES},
        "reminders": reminders, "obsidian": obsidian, "jira": jira,
    }
    dl = datetime.combine(today + timedelta(days=4), datetime.min.time())
    events.append({"title": "Deadline: conference full paper", "start": iso(dl), "end": iso(dl + timedelta(days=1)), "all_day": True,
                   "calendar": "Work", "location": "", "free": True, "id": "demo-deadline"})
    data["health"] = {k: {"state": "ok", "synced": iso(now), "count": c, "ms": t, "error": "", "hint": "", "carried": False}
                      for k, c, t in (("calendar", len(events), 1840), ("reminders", len(reminders["open"]), 1840),
                                      ("obsidian", len(obsidian["notes"]), 95), ("jira", len(jira["issues"]), 610))}
    return data, period
