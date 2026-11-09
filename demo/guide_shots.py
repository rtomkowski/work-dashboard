"""Screenshots for docs/GUIDE.md from the demo server (made-up data only).

    python3 demo/serve_demo.py &
    python3 demo/guide_shots.py            # writes docs/guide/*.png

Needs Python Playwright with Chromium; Pillow (optional) shrinks the PNGs. Each shot loads the page fresh, runs a short script, grows the window until no scroll area hides anything (whole dashboard, up to 4000 px tall), then captures.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
OUT = Path(__file__).resolve().parent.parent / "docs" / "guide"
OUT.mkdir(parents=True, exist_ok=True)

DESK = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}


def api(path: str, body: dict | None = None) -> None:
    """POST to the demo server (in-memory state only)."""
    req = urllib.request.Request(URL + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:  # a refused action is fine for cleanup
        print("  api", path, e)


# name, script, theme, (optional) settle seconds
SHOTS = [
    # views
    ("02-day", 'setView("day")', "light"),
    ("03-event-panel", 'setView("day"); evOpen(DATA.calendar.events.find(e => e.title === "Project review with partners" && same(P(e.start), new Date())).id)', "light"),
    ("04-meeting-prep", 'setView("day"); const e = DATA.calendar.events.filter(x => x.title === "Project sync: Surface Lab" && P(x.start) > new Date()).sort((a, b) => P(a.start) - P(b.start))[0]; if (e) { S.anchor = sod(P(e.start)); evOpen(e.id); }', "light"),
    ("05-week", 'setView("week")', "light"),
    ("05b-week-grid", 'S.weekLay = "cols"; setView("week")', "light"),
    ("06-month", 'setView("month")', "light"),
    ("07-year", 'setView("year")', "light"),
    ("08-board", 'setView("board")', "light"),
    ("09-overdue", 'setView("overdue")', "light"),
    ("10-stats-history", 'ST.mode = "history"; setView("stats")', "light"),
    ("11-stats-ahead", 'ST.mode = "ahead"; setView("stats")', "light"),
    ("12-weekly-review", 'setView("review")', "light"),
    # find and capture
    ("13-palette-search", 'setView("week"); openPal(); $("palQ").value = "lab"; $("palQ").oninput()', "light"),
    ("14-palette-capture", 'setView("week"); openPal(); $("palQ").value = "+ Ask Anna for the gear sample IDs"; $("palQ").oninput()', "light"),
    # create
    ("15-new-reminders", 'setView("week"); openRem()', "light"),
    ("16-plan-blocks", 'setView("week"); openPlan()', "light"),
    ("17-new-note", 'setView("day"); openNote({title: "Idea: ML for defect detection"})', "light"),
    ("18-inbox-triage", 'setView("week"); openInbox()', "light"),
    ("19-new-jira-issue", 'setView("board"); openJira()', "light"),
    # timers
    ("20-countdown-set", 'setView("week"); cdOpen(); $("cdTxt").value = "Coffee with PhD students"', "light"),
    ("21-countdown-running", 'setView("week"); await cdPost({action: "add", minutes: 20, label: "Review draft"}); cdOpen()', "light"),
    ("22-focus-timer", 'setView("board"); await tmStart("DEMO-12", "Calibrate the new confocal microscope"); await new Promise(r => setTimeout(r, 1200))', "light"),
    ("23-focus-timer-stop", 'setView("board"); await tmSync(); tmOpenStop()', "light"),
    # settings and health
    ("24-settings-profiles", 'setView("week"); await openPrefs("profiles")', "light"),
    ("25-settings-panel", 'setView("week"); await openPrefs("panel")', "light"),
    ("26-settings-data", 'setView("week"); await openPrefs("data")', "light"),
    ("27-data-sources", 'setView("week"); openHealth()', "light"),
    ("28-source-failing", 'DATA.health.jira = Object.assign({}, DATA.health.jira, {state: "error", error: "HTTP 401 Unauthorized", hint: "The API token in the Keychain was rejected. Create a new token and store it with security add-generic-password.", carried: true}); setView("week"); render()', "light"),
    ("29-week-dark", 'setView("week")', "dark"),
]

# iPhone Safari has no Fullscreen API, so the real page hides the full screen button; Chromium has it, so hide it here
PHONE_SHOTS = [
    ("30-phone-today", 'setView("day"); $("fsBtn").hidden = true'),
    ("31-phone-week", 'setView("week"); $("fsBtn").hidden = true'),
]


def shrink(path: Path) -> None:
    """256-colour palette PNG: about a quarter of the size, no visible loss on UI shots (keeps the repo light)."""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


# Extra height the page needs so nothing is cut off: the largest hidden overflow of any scroll area
# (main column, sidebar, right panel, an open dialog) or of the document itself.
OVERFLOW_JS = """() => {
  let extra = document.documentElement.scrollHeight - innerHeight;
  for (const e of document.querySelectorAll("*")) {
    const s = getComputedStyle(e);
    if (/(auto|scroll)/.test(s.overflowY) && e.clientHeight > 60 && e.offsetParent !== null)
      extra = Math.max(extra, e.scrollHeight - e.clientHeight);
  }
  return Math.max(0, Math.ceil(extra));
}"""
UNCAP_JS = """() => {
  // Open dialogs cap their height in px and scroll inside; lift the caps so the whole dialog is in the picture.
  for (const d of document.querySelectorAll("dialog[open]")) {
    d.style.maxHeight = "none";
    for (const e of d.querySelectorAll("*")) {
      const s = getComputedStyle(e);
      if (/(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 2) {
        for (let a = e; a && a !== d; a = a.parentElement) { a.style.maxHeight = "none"; a.style.height = "auto"; a.style.flex = "none"; }
        e.style.overflowY = "visible";
      }
    }
  }
}"""
MAX_H = 4000
BASE: dict = {}  # page -> starting viewport (1440x900 desktop, iPhone 13 phone)


def fit_height(page, base: dict) -> None:
    """Grow the viewport until every scroll area shows all of its content (whole dashboard in one shot)."""
    page.evaluate(UNCAP_JS)
    for _ in range(4):  # layouts reflow when the window grows, so measure again
        extra = page.evaluate(OVERFLOW_JS)
        if extra <= 2:
            return
        size = page.viewport_size
        page.set_viewport_size({"width": size["width"], "height": min(MAX_H, size["height"] + extra)})
        time.sleep(0.6)
        if page.viewport_size["height"] >= MAX_H:
            return


def shoot(page, name: str, script: str, theme: str) -> None:
    page.set_viewport_size(BASE[page])
    page.emulate_media(color_scheme=theme)
    page.goto(URL)
    page.wait_for_function("typeof DATA !== 'undefined' && DATA && DATA.calendar")
    time.sleep(0.8)
    page.evaluate(f'async () => {{ L.theme = "{theme}"; applyTheme(); {script}; }}')
    time.sleep(1.2)
    fit_height(page, BASE[page])
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    shrink(path)
    print("wrote", name)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(**DESK)
    page = ctx.new_page()
    BASE[page] = dict(page.viewport_size)
    page.on("pageerror", lambda e: print("  pageerror:", e))
    page.on("dialog", lambda d: d.accept())
    only = set(sys.argv[2:])
    for name, script, theme in SHOTS:
        if only and name not in only:
            continue
        shoot(page, name, script, theme)
        if name == "21-countdown-running":
            page.evaluate('async () => { await cdPost({action: "cancel", id: CD.a?.id}); }')
        if name == "23-focus-timer-stop":
            api("api/timer", {"action": "discard"})
    ctx.close()
    ctx = b.new_context(**p.devices["iPhone 13"])
    page = ctx.new_page()
    BASE[page] = dict(page.viewport_size)
    for name, script in PHONE_SHOTS:
        if only and name not in only:
            continue
        shoot(page, name, script, "light")
    b.close()
