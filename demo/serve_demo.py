"""Demo server: the real dashboard.html with made-up data (demo_data.py), for screenshots and trying the page
without Calendar, Reminders, Obsidian or Jira access. Nothing is written to disk or sent anywhere: the board,
new notes, capture, Inbox triage, new Jira issues, the focus timer and alarms work in memory until the server stops;
Plan blocks, New reminders and settings are refused.

    python3 demo/serve_demo.py            # http://127.0.0.1:8766
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import ahead      # noqa: E402  pure Python, no EventKit
import alarms     # noqa: E402
import planner    # noqa: E402
import stats      # noqa: E402
from demo_data import build  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
CFG = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
ST = CFG.get("stats") or {}
DATA, EVENTS = build()
NOTES_MADE, ISSUES_MADE, CAPTURED, MOVES = [], [], [], []   # demo writes, in memory only
TIMER = {"t": None}
ALARMS = alarms.Alarms(None)   # in memory; rings on the page only, no Mac notification
# v0.46 (KAN-87): work from home on in the demo, with one home day this week (in memory)
_mon = datetime.now().date() - timedelta(days=datetime.now().weekday())
WFH = {str(_mon + timedelta(days=2)): "home"}
GLOBAL = {"wfh": True, "wfh_cal": True}
KANBAN = {"rem:d6": {"col": "doing", "t": DATA["generated"]}, "obs:a1": {"col": "doing", "t": DATA["generated"]}}
SETTINGS = {"refresh_minutes": 5, "calendar": {"days_back": 35, "days_ahead": 70},
            "weather": {"enabled": True, "place": "Stockholm", "lat": 59.35, "lon": 18.07},
            "jira": {"enabled": True, "jql": "", "configured": True}, "work_hours": ST.get("work_hours", [8, 18])}
WEATHER = {"enabled": True, "place": "Stockholm", "temp": 7.4, "code": 2, "wind": 11.0, "is_day": 1,
           "max": 9.1, "min": 3.2, "rain": 20}


def version() -> str:
    for line in (ROOT / "server.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION"):
            return line.split('"')[1] + " demo"
    return "demo"


def page_data() -> bytes:
    return json.dumps(dict(DATA, kanban=KANBAN, wfh=WFH, service_started=DATA["generated"], version=version()),
                      ensure_ascii=False).encode()


def inbox_notes() -> list:
    """Inbox notes for Inbox triage, minus the ones moved in this session."""
    moved = {m["from"] for m in MOVES}
    out = []
    for n in DATA["obsidian"]["notes"]:
        if n["folder"] != "Inbox" or f"Inbox/{n['title']}.md" in moved:
            continue
        born = datetime.fromisoformat(n.get("created") or n["modified"])
        out.append({"title": n["title"], "path": f"Inbox/{n['title']}.md", "uri": n["uri"], "created": n.get("created"),
                    "modified": n["modified"], "age_days": (datetime.now() - born).days, "excerpt": n["excerpt"]})
    return sorted(out, key=lambda x: x["created"] or "")


def stats_payload(q: dict) -> dict:
    kind = (q.get("period") or [ST.get("default_period", "this_year")])[0]
    if kind == "calendar_year":
        kind = "this_year"
    s, e, ps, pe, label = stats.period_range(kind, date.today(), (q.get("from") or [""])[0], (q.get("to") or [""])[0])
    cur, prev = stats.compute(EVENTS, ST, s, e), stats.compute(EVENTS, ST, ps, pe)
    keep = ("period", "total_hours", "weekly", "utilisation", "focus_blocks_per_week", "groups")
    return {"label": label, "kind": kind, "current": cur, "previous": {k: prev[k] for k in keep},
            "summary": stats.summarise(cur, prev, label), "vacation_weeks": ST.get("vacation_weeks", []),
            "generated": datetime.now().strftime("%Y-%m-%dT%H:%M")}


def free_days(q: dict) -> dict:
    now = datetime.now()
    last = now.date() + timedelta(weeks=2)
    until = (q.get("until") or [""])[0]
    if until:
        last = max(last, min(date.fromisoformat(until), now.date() + timedelta(weeks=planner.MAX_WEEKS)))
    wl = planner.week_load(ahead.forecast(EVENTS, ST, now, weeks=planner.MAX_WEEKS + 1))
    return planner.Planner(EVENTS, ST, now, last, wl, 15)


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

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        q = parse_qs(qs)
        if path in ("/", "/index.html"):
            self._send((ROOT / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/stamp":
            self._json({"stamp": 0})
        elif path == "/data.json":
            self._send(page_data(), "application/json; charset=utf-8")
        elif path == "/api/stats":
            self._json(stats_payload(q))
        elif path == "/api/ahead":
            self._json(ahead.forecast(EVENTS, ST, datetime.now(), weeks=int((q.get("weeks") or ["8"])[0])))
        elif path == "/api/weather":
            self._json(WEATHER)
        elif path in ("/api/plan/last", "/api/reminders/last"):
            self._json({"last_batch": None, "last": None})
        elif path == "/api/reminders/days":
            out = free_days(q).plan([])
            out.pop("rows", None)
            out["last"] = None
            self._json(out)
        elif path == "/api/search":
            words = ((q.get("q") or [""])[0]).lower().split()
            ns = [n for n in DATA["obsidian"]["notes"] if words and all(w in (n["title"] + " " + n["excerpt"]).lower() for w in words)]
            self._json({"notes": [{"title": n["title"], "folder": n["folder"], "uri": n["uri"], "snippet": n["excerpt"]} for n in ns]})
        elif path == "/api/notes/targets":
            self._json({"folders": ["Inbox", "Learning", "Research/NOTES", "Research/NOTES/Meetings"],
                        "templates": [{"id": k, "name": v, "builtin": True} for k, v in
                                      (("meeting", "Meeting note"), ("idea", "Idea"), ("literature", "Literature note"),
                                       ("blank", "Blank"))] + [{"id": "user:Project page", "name": "Project page", "builtin": False}],
                        "default": {"note": "Inbox", "meeting": "Research/NOTES/Meetings", "review": "Inbox"},
                        "vault": "Demo"})
        elif path == "/api/timer":
            self._json({"timer": TIMER.get("t")})
        elif path == "/api/alarms":
            ALARMS.tick()
            self._json(dict(ALARMS.state(), snooze=5))
        elif path == "/api/jira/epics":
            self._json({"enabled": True, "default": "DEMO-1", "epics": [{"key": "DEMO-1", "name": "Lab upgrade"}, {"key": "DEMO-2", "name": "Publications"},
                                                    {"key": "DEMO-3", "name": "Teaching"}]})
        elif path == "/api/inbox":
            self._json({"folder": "Inbox", "targets": ["Learning", "Research/NOTES", "Research/NOTES/Meetings"],
                        "last": MOVES[-1] if MOVES else None, "notes": inbox_notes()})
        elif path == "/api/profiles":
            self._json({"profiles": None, "global": GLOBAL})
        elif path == "/api/settings":
            self._json(SETTINGS)
        elif path == "/api/vault":
            self._json({"vault": "~/Demo vault", "selected": {"folders": ["Inbox", "Research/NOTES", "Learning"],
                                                              "files": [], "exclude": []},
                        "folders": [{"path": p, "depth": p.count("/"), "md": n} for p, n in
                                    (("Inbox", 12), ("Learning", 30), ("Research", 41), ("Research/NOTES", 38))]})
        else:
            self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if path == "/refresh":
            return self._send(page_data(), "application/json; charset=utf-8")
        if path == "/api/wfh":
            if body.get("state") in ("home", "office"):
                WFH[str(body.get("date"))] = body["state"]
            else:
                WFH.pop(str(body.get("date")), None)
            return self._json({"wfh": WFH})
        if path == "/api/reminders/hold":   # v0.47: the notes mark changes in memory only
            import harvest
            changed, skipped = [], []
            for r in DATA["reminders"]["open"]:
                if r["id"] in (body.get("ids") or []):
                    if harvest.is_on_hold(r["notes"]) == bool(body.get("hold")):
                        skipped.append({"id": r["id"], "why": "already " + ("on hold" if body.get("hold") else "active")}); continue
                    r["notes"] = harvest._with_hold(r["notes"]) if body.get("hold") else harvest._strip_hold(r["notes"])
                    r["hold"] = harvest.is_on_hold(r["notes"]); changed.append(r["id"])
            d = json.loads(page_data())
            d["held"] = {"hold": bool(body.get("hold")), "changed": changed, "skipped": skipped}
            return self._json(d)
        if path == "/api/kanban":
            if body.get("col") in (None, "", "auto"):
                KANBAN.pop(body.get("id"), None)
            else:
                KANBAN[body["id"]] = {"col": body["col"], "t": datetime.now().strftime("%Y-%m-%dT%H:%M")}
            return self._json({"kanban": KANBAN})
        if path == "/api/notes":
            # demo: pretend; nothing is written
            if not str(body.get("title") or "").strip():
                return self._json({"error": "Give the note a title"}, 400)
            NOTES_MADE.append(body)
            rel = f"{body.get('folder')}/{body['title']}.md"
            return self._json({"name": body["title"], "path": rel, "renamed": False,
                               "uri": "obsidian://open?vault=Demo&file=" + rel[:-3].replace(" ", "%20"), "demo": True})
        if path == "/api/review/auto":
            # demo: never writes; the weekly review is only written by the real dashboard
            return self._json({"skipped": "demo"})
        if path == "/api/alarms":
            try:
                ALARMS.tick()
                return self._json(ALARMS.act(body))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/timer":   # demo: kept in memory, nothing goes to Jira
            if body.get("action") == "start":
                TIMER["t"] = {"key": body.get("key"), "title": body.get("title", ""), "started": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
                return self._json({"timer": TIMER["t"]})
            TIMER["t"] = None
            return self._json({"timer": None, "worklog": {"key": "DEMO", "seconds": int(body.get("minutes") or 0) * 60}} if body.get("action") == "stop" else {"timer": None})
        if path == "/api/jira/hold":   # v0.50: the hold label changes in memory only
            for i in DATA["jira"]["issues"]:
                if i["key"] == body.get("key"):
                    labels = [x for x in i.get("labels", []) if x.lower() not in ("hold", "onhold", "on-hold")]
                    i["labels"] = labels + (["hold"] if body.get("hold") else [])
                    i["hold"] = i["hold_label"] = bool(body.get("hold"))
            return self._send(page_data(), "application/json; charset=utf-8")
        if path == "/api/jira/issue":
            if not str(body.get("summary") or "").strip():
                return self._json({"error": "Give the issue a summary"}, 400)
            key = f"DEMO-{100 + len(ISSUES_MADE)}"
            ISSUES_MADE.append(dict(body, key=key))
            return self._json({"key": key, "url": "https://example.atlassian.net/browse/" + key})
        if path == "/api/capture":
            text = " ".join(str(body.get("text") or "").split())
            if not text:
                return self._json({"error": "Nothing to capture"}, 400)
            line = f"- [ ] {text}" if body.get("task") else f"- {datetime.now():%Y-%m-%d %H:%M} {text}"
            CAPTURED.append(line)
            name = "List from Work Dashboard" if body.get("task") else "Capture from Work Dashboard"
            return self._json({"path": f"Inbox/WorkDashboard/{name}.md",
                               "uri": "obsidian://open?vault=Demo&file=" + quote(f"Inbox/WorkDashboard/{name}"),
                               "line": line, "created": sum(1 for c in CAPTURED if c.startswith("- [ ]") == bool(body.get("task"))) == 1})
        if path == "/api/inbox/move":
            n = next((x for x in inbox_notes() if x["path"] == body.get("path")), None)
            if not n or not body.get("folder"):
                return self._json({"error": "Pick a folder first"}, 400)
            to = f"{body['folder']}/{n['title']}.md"
            MOVES.append({"from": n["path"], "to": to})
            return self._json({"from": n["path"], "to": to, "uri": "obsidian://open?vault=Demo&file=" + to[:-3].replace(" ", "%20")})
        if path == "/api/inbox/undo":
            if not MOVES:
                return self._json({"error": "Nothing to undo"}, 400)
            m = MOVES.pop()
            return self._json({"from": m["to"], "to": m["from"]})
        if path == "/api/plan/suggest":
            items = planner.clean_items(body.get("rows"), date.today(), stats.Rules(ST), [])
            out = free_days({"until": [str(body.get("until") or "")]}).plan(items)
            out.update(calendar="Work", categories=[], outlook={"ok": False, "error": "Demo mode", "calendar": "Work"},
                       last_batch=None)
            return self._json(out)
        self._json({"error": "Demo mode: nothing is written"}, 400)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"WorkDashboard demo on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
