"""Plan blocks (KAN-61): you choose the date and time of each block; free time is offered as help.

Pure Python like stats.py and ahead.py; harvest.py reads and writes EventKit. Busy time is what stats.py counts
(working window, overlaps once, 'free' events and Ignore left out), with tentative invitations counted as busy.
Rows with a date and start time are checked for clashes with the calendar and with each other. Every row also
gets a few ranked free times (this and next week, closest to its date first) that avoid the other rows. A row may
carry an Outlook category (set by outlook.py when booking); titles are booked as typed.

Ranking of free times, lower score is better:
- fit: a short block goes into a gap of its size, so free 2 h stretches stay whole; no tiny leftovers;
- buffer: keep `buffer` minutes away from meetings (the working window edges need none);
- load: days with little booked and weeks the Ahead forecast expects to be light come first;
- habit: hours that usually fill up with meetings (last 26 weeks) are avoided;
- time: earlier is a little better, and close to the chosen date when there is one; Focus time leans to the morning.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from stats import Rules, _p, _resolve, _slots, iso_week, swedish_holidays

STEP = 15                # minutes between candidate start times
FOCUS = 120              # a free stretch of 2 h or more is a focus block
MAX_WEEKS = 8


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _dur(m: int) -> str:
    return f"{m // 60} h {m % 60} min" if m % 60 and m > 60 else f"{m // 60} h" if m >= 60 else f"{m} min"


def _merge(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


# ---------------------------------------------------------------- input

def window(day: str, today: date):
    """Dates a row's free times come from: this and next week, and a week past its own date."""
    mon = today - timedelta(days=today.weekday())
    last = mon + timedelta(days=11)
    if day:
        last = max(last, date.fromisoformat(day) + timedelta(days=7))
    return today, min(last, today + timedelta(weeks=MAX_WEEKS))


def clean_items(rows, today: date, rules: Rules, categories=()) -> list:
    """Validate the rows from the page. Raises ValueError with a message the page can show.
    categories: the Outlook category names on offer; anything else is dropped (e.g. from an older draft)."""
    known = {c.lower(): c for c in categories}
    if not isinstance(rows, list) or not rows:
        raise ValueError("Add at least one block")
    if len(rows) > 20:
        raise ValueError("At most 20 rows at a time")
    out, seen = [], set()
    for n, r in enumerate(rows, 1):
        rid = str(r.get("id") or n)[:40]
        if rid in seen:
            raise ValueError(f"Block {n}: duplicate id")
        seen.add(rid)
        title = str(r.get("title") or "").strip()
        if len(title) > 300:
            raise ValueError(f"Block {n}: title is too long (max 300 characters)")
        minutes = int(r.get("minutes") or 60)
        if not 15 <= minutes <= 600 or minutes % STEP:
            raise ValueError(f"Block {n}: length must be 15 min to 10 h in steps of 15 min")
        day, tm = str(r.get("date") or ""), str(r.get("time") or "")
        if day:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError(f"Block {n}: the date must be YYYY-MM-DD")
            if date.fromisoformat(day) < today:
                raise ValueError(f"Block {n}: the date is in the past")
        if tm:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", tm):
                raise ValueError(f"Block {n}: the start time must be HH:MM")
            if not day:
                raise ValueError(f"Block {n}: a start time needs a date")
        cat = known.get(str(r.get("category") or "").strip().lower(), "")
        bt = title or "Blocked time"
        out.append({"id": rid, "title": title, "minutes": minutes, "date": day, "time": tm, "category": cat,
                    "book_title": bt, "focus": rules.category(bt) == "Focus time" or rules.group(rules.category(bt)) == "Focus time"})
    return out


def horizon(items, today: date) -> date:
    """Last day to read: every row's range and date."""
    return max([window(i["date"], today)[1] for i in items] + [today])


# ---------------------------------------------------------------- planner

class Planner:
    def __init__(self, events, st: dict, now: datetime, last: date, week_load=None, buffer: int = 15):
        self.rules = Rules(st)
        self.w0, self.w1 = st.get("work_hours") or [8, 18]
        self.lo, self.hi = self.w0 * 60, self.w1 * 60
        self.buffer = buffer
        self.now, self.today, self.last = now, now.date(), last
        self.vac = set(st.get("vacation_weeks", []))
        self.hol = {}
        for y in range(self.today.year - 1, last.year + 1):
            self.hol.update(swedish_holidays(y))
        self.week_load = week_load or {}
        # tentative invitations count as busy: the slot may well be taken
        evs = [dict(e, avail="busy") if e.get("avail") == "tentative" else e for e in events]
        self.raw = [e for e in evs if not e.get("all_day") and e.get("avail") != "free"
                    and self.rules.category(e.get("title") or "") not in ("Ignore",)]
        self.allday = [e for e in evs if e.get("all_day") and e.get("avail") != "free"
                       and self.rules.category(e.get("title") or "") not in ("Ignore",)]
        self.taken_all = defaultdict(list)
        monday = self.today - timedelta(days=self.today.weekday())   # the week strip starts on Monday
        days, self.absence = _slots(evs, self.rules, monday, last, self.w0, self.w1)
        self.busy = {d: _merge((a, b) for a, b, *_ in _resolve(items)) for d, items in days.items()}
        self.hour_busy = self._habit(evs)
        n = now.hour * 60 + now.minute
        self.now_min = -(-n // STEP) * STEP

    def _habit(self, events):
        """Share of past working days (26 weeks) on which each working hour was booked."""
        start = self.today - timedelta(weeks=26)
        days, absence = _slots(events, self.rules, start, self.today - timedelta(days=1), self.w0, self.w1)
        cnt = [0.0] * (self.w1 - self.w0)
        n = 0
        hol = {}
        for y in range(start.year, self.today.year + 1):
            hol.update(swedish_holidays(y))
        d = start
        while d < self.today:
            if d.weekday() < 5 and d not in hol and d not in absence and iso_week(d) not in self.vac:
                n += 1
                for a, b, *_ in _resolve(days.get(d, [])):
                    for h in range(self.w0, self.w1):
                        cnt[h - self.w0] += max(0, min(b, (h + 1) * 60) - max(a, h * 60)) / 60
            d += timedelta(days=1)
        return [c / n for c in cnt] if n else [0.0] * (self.w1 - self.w0)

    def off_reason(self, d: date):
        if d.weekday() >= 5:
            return "weekend"
        if d in self.hol:
            return self.hol[d]
        if iso_week(d) in self.vac:
            return "vacation"
        if d in self.absence:
            return "absence"
        return ""

    def booked_min(self, d: date) -> int:
        return sum(b - a for a, b in self.busy.get(d, []))

    def gaps(self, d: date, taken):
        """Free stretches (a, b, event_left, event_right) in the working window."""
        start = self.lo if d != self.today else max(self.lo, self.now_min)
        busy = _merge(self.busy.get(d, []) + taken.get(d, []))
        out, t, ev = [], start, False
        for a, b in busy + [(self.hi, self.hi)]:
            if a > t:
                out.append((t, min(a, self.hi), ev, a < self.hi))
            if b > t:
                t, ev = b, True
            if t >= self.hi:
                break
        return [g for g in out if g[1] > g[0]]

    def _score(self, d, gap, s, e, unit):
        a, b, lev, rev = gap
        dur, glen, left, right = e - s, b - a, s - a, b - e
        pen, why = 0.0, []
        for side, ev in ((left, lev), (right, rev)):
            if ev and side < self.buffer:
                pen += (self.buffer - side) * 2
        lost = max(0, int(glen >= FOCUS) - int(left >= FOCUS) - int(right >= FOCUS)) if dur < FOCUS else 0
        pen += 40 * lost
        # leftovers, not counting the buffer kept next to a meeting
        ul, ur = left - (self.buffer if lev else 0), right - (self.buffer if rev else 0)
        pen += sum(6 for p in (ul, ur) if 0 < p < 30)
        if ul >= 30 and ur >= 30:
            pen += 6
        booked = self.booked_min(d)
        pen += 20 * booked / (self.hi - self.lo)
        wl = self.week_load.get((d - timedelta(days=d.weekday())).isoformat())
        if wl is not None:
            pen += 15 * min(1.2, wl)
        hrs = [self.hour_busy[h - self.w0] for h in range(s // 60, -(-e // 60)) if self.w0 <= h < self.w1]
        habit = sum(hrs) / len(hrs) if hrs else 0.0
        pen += 15 * habit
        pen += 0.5 * (d - unit["first"]).days
        if unit.get("near"):                 # the date you chose: that day first, then close to it
            pen += 2 * abs((d - unit["near"]).days)
        if unit["focus"] and s < 12 * 60:
            pen -= 4
        # plain-language reasons, most telling first
        if glen - dur <= 2 * self.buffer + STEP and lev:
            why.append(f"fits a {_dur(glen)} gap")
        elif dur < FOCUS and glen >= FOCUS and not lost:
            why.append("keeps your 2 h blocks whole")
        elif dur >= FOCUS:
            why.append("uses a free focus block")
        why.append(f"{booked / 60:.1f} h booked that day".replace(".0 h", " h"))
        if wl is not None and wl < 0.6:
            why.append("light week")
        if habit < 0.2:
            why.append("usually a quiet hour")
        return pen, why

    def candidates(self, unit, taken):
        out = []
        d = unit["first"]
        while d <= unit["last"]:
            if not self.off_reason(d):
                for gap in self.gaps(d, taken):
                    a, b = gap[0], gap[1]
                    s = -(-a // STEP) * STEP
                    while s + unit["minutes"] <= b:
                        pen, why = self._score(d, gap, s, s + unit["minutes"], unit)
                        out.append((pen, d, s, s + unit["minutes"], why))
                        s += STEP
            d += timedelta(days=1)
        out.sort(key=lambda c: (c[0], c[1], c[2]))
        return out

    def clashes(self, d, s, e, others):
        """Titles of calendar events and other rows overlapping [s, e) on day d."""
        a, b = datetime.combine(d, datetime.min.time()) + timedelta(minutes=s), datetime.combine(d, datetime.min.time()) + timedelta(minutes=e)
        hit = [ev["title"] or "(no title)" for ev in self.raw if _p(ev["start"]) < b and a < _p(ev["end"])]
        hit += [f"block {n}" for n, (od, os_, oe) in others if od == d and os_ < e and s < oe]
        return list(dict.fromkeys(hit))

    def place(self, items):
        """Check rows that have a time; offer free times for every row."""
        rows, fixed = [], []
        for n, it in enumerate(items, 1):
            out = {"id": it["id"], "n": n, "title": it["book_title"], "minutes": it["minutes"], "status": "unset",
                   "start": None, "end": None, "info": [], "clash": [], "options": [],
                   "category": self.rules.category(it["book_title"]), "chosen": it["category"]}
            if it["time"]:
                d = date.fromisoformat(it["date"])
                s = int(it["time"][:2]) * 60 + int(it["time"][3:])
                e = s + it["minutes"]
                out["start"] = f"{d.isoformat()}T{_hm(s)}"
                out["end"] = f"{d.isoformat()}T{_hm(min(e, 24 * 60 - 1))}"
                if e > 24 * 60:
                    out.update(status="clash", clash=["runs past midnight"])
                elif datetime.combine(d, datetime.min.time()) + timedelta(minutes=s) < self.now - timedelta(minutes=5):
                    out.update(status="clash", clash=["the time has passed"])
                else:
                    out["status"] = "ok"
                    off = self.off_reason(d)
                    out["info"] = ([off] if off else []) + (["outside working hours"] if s < self.lo or e > self.hi else [])
                    fixed.append((n, it, d, s, e))
            rows.append(out)
        for n, it, d, s, e in fixed:
            others = [(m, (d2, s2, e2)) for m, _, d2, s2, e2 in fixed if m != n]
            hit = self.clashes(d, s, e, others)
            if hit:
                rows[n - 1].update(status="clash", clash=hit)
        # free times for each row, avoiding the other rows' chosen times
        for n, it in enumerate(items, 1):
            taken = defaultdict(list)
            for m, _, d2, s2, e2 in fixed:
                if m != n:
                    taken[d2].append((s2, e2))
            first, last = window(it["date"], self.today)
            unit = {"minutes": it["minutes"], "focus": it["focus"], "first": first, "last": last,
                    "near": date.fromisoformat(it["date"]) if it["date"] else None}
            cands, opts = self.candidates(unit, taken), []
            if unit["near"]:                   # free times on the chosen day come first, then the rest
                cands.sort(key=lambda c: c[1] != unit["near"])
            for c in cands:
                if all(c[1] != x[1] or abs(c[2] - x[2]) >= 60 for x in opts):
                    opts.append(c)
                if len(opts) == 5:
                    break
            rows[n - 1]["options"] = [{"date": c[1].isoformat(), "time": _hm(c[2]), "end": _hm(c[3]), "why": c[4][:3]}
                                      for c in opts]
        for _, _, d, s, e in fixed:
            self.taken_all[d].append((s, e))
        return rows

    def _day_events(self, d: date):
        """Timed events on day d as (start, end, title) in minutes, clipped to the day, plus all-day titles."""
        d0 = datetime.combine(d, datetime.min.time())
        out = []
        for ev in self.raw:
            a, b = _p(ev["start"]), _p(ev["end"])
            if a < d0 + timedelta(days=1) and d0 < b:
                out.append([max(0, int((a - d0).total_seconds() // 60)), min(24 * 60, int((b - d0).total_seconds() // 60)),
                            ev.get("title") or "(no title)"])
        allday = [ev.get("title") or "(no title)" for ev in self.allday
                  if _p(ev["start"]).date() <= d and (d < _p(ev["end"]).date() or _p(ev["start"]).date() == d)]
        return sorted(out), allday

    def days(self):
        """Per weekday from this Monday to the horizon: events with titles, booked hours and free stretches."""
        out = {}
        d = self.today - timedelta(days=self.today.weekday())
        while d <= self.last or d.weekday() != 0:
            if d.weekday() < 5:
                evs, allday = self._day_events(d)
                free = [[a, b] for a, b, *_ in self.gaps(d, self.taken_all)] if d >= self.today and not self.off_reason(d) else []
                out[d.isoformat()] = {"events": evs, "allday": list(dict.fromkeys(allday)), "off": self.off_reason(d),
                                      "booked": round(self.booked_min(d) / 60, 1), "free": free}
            d += timedelta(days=1)
        return out

    def plan(self, items) -> dict:
        rows = self.place(items)              # fills taken_all, which the free stretches in days() avoid
        return {"rows": rows, "days": self.days(), "work_hours": [self.w0, self.w1],
                "today": self.today.isoformat(), "now_min": self.now_min, "buffer": self.buffer}


# ---------------------------------------------------------------- booking check

def conflicts(events, blocks, st: dict) -> list:
    """Blocks that overlap a timed event that takes time (not free, not declined, not Ignore). Raw events, no
    working window: booking is checked against everything that is in the calendar right now."""
    rules = Rules(st)
    out = []
    for i, bl in enumerate(blocks):
        s, e = _p(bl["start"]), _p(bl["end"])
        hit = [ev["title"] for ev in events
               if not ev.get("all_day") and ev.get("avail") != "free" and rules.category(ev["title"]) != "Ignore"
               and _p(ev["start"]) < e and s < _p(ev["end"])]
        if hit:
            out.append({"i": i, "with": hit[:3]})
    return out


def week_load(fc: dict) -> dict:
    """Ahead forecast -> {monday: expected share of available hours}."""
    return {w["monday"]: w["expected_total"] / w["available"] for w in fc.get("weeks", []) if w.get("available")}
