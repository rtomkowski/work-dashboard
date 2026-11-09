"""Ahead view (KAN-55): available time in the coming weeks and the load to expect, learned from history.

Uses the same counting as stats.py (title rules, working window, overlaps once). Events may carry "created"
("YYYY-MM-DDTHH:MM", from EventKit creationDate). For every past week we measure how many hours were already
booked k weeks before it started and how many were added later; a future week k weeks ahead is expected to get
the typical (median) late additions for that lead time. If creation dates look unusable the forecast falls back
to recent comparable weeks. Nothing here writes to the calendar.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from stats import Rules, _p, _resolve, _slots, iso_week, swedish_holidays

MIN_BLOCK = 120          # minutes: a "free block" is at least 2 h
FULL = 0.9               # expected load at or above 90 % of available time = full


def _q(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)


class _Cal:
    """Events indexed by week, so each week is resolved from its own events only."""

    def __init__(self, events, rules: Rules, w0: int, w1: int):
        self.rules, self.w0, self.w1 = rules, w0, w1
        self.by_week = defaultdict(list)
        for ev in events:
            s, e = _p(ev["start"]), _p(ev["end"])
            m = s.date() - timedelta(days=s.weekday())
            while m <= e.date():
                self.by_week[m].append(ev)
                m += timedelta(days=7)

    def week(self, monday: date, created_before: datetime | None = None):
        """Resolved Mon-Fri slots per day, optionally only events created before a moment."""
        evs = self.by_week.get(monday, [])
        if created_before is not None:
            evs = [e for e in evs if not e.get("created") or _p(e["created"]) <= created_before]
        days, absence = _slots(evs, self.rules, monday, monday + timedelta(days=4), self.w0, self.w1)
        return {d: _resolve(items) for d, items in days.items()}, absence


def _hours(resolved: dict, keep_days, rules: Rules):
    tot, grp = 0.0, Counter()
    for d, segs in resolved.items():
        if d in keep_days:
            for a, b, cat, *_ in segs:
                h = (b - a) / 60
                tot += h
                grp[rules.group(cat)] += h
    return tot, grp


def _free_blocks(segs, w0, w1, start_min=None):
    """Free stretches of at least MIN_BLOCK minutes inside the working window."""
    t = w0 * 60 if start_min is None else max(w0 * 60, start_min)
    out = []
    for a, b in sorted((s[0], s[1]) for s in segs) + [(w1 * 60, w1 * 60)]:
        if a - t >= MIN_BLOCK:
            out.append((t, a))
        t = max(t, b)
    return out


def _hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def forecast(events, st: dict, now: datetime, weeks: int = 8, history_weeks: int = 26, vacation_weeks=None) -> dict:
    rules = Rules(st)
    w0, w1 = st.get("work_hours") or [8, 18]
    nominal = float(st.get("nominal_hours_per_day", 8))
    vac = set(vacation_weeks if vacation_weeks is not None else st.get("vacation_weeks", []))
    today = now.date()
    this_mon = today - timedelta(days=today.weekday())
    hol = {}
    for y in range(this_mon.year - 2, this_mon.year + 2):
        hol.update(swedish_holidays(y))
    cal = _Cal(events, rules, w0, w1)

    def workdays(monday, absence, from_day=None):
        return [monday + timedelta(days=i) for i in range(5)
                if (from_day is None or monday + timedelta(days=i) >= from_day)
                and iso_week(monday) not in vac and monday + timedelta(days=i) not in hol
                and monday + timedelta(days=i) not in absence]

    # ------------------------------------------------ history: final load and late additions per lead time
    hist = []
    known = with_created = 0
    for j in range(1, history_weeks + 1):
        mon = this_mon - timedelta(weeks=j)
        if iso_week(mon) in vac:
            continue
        res, absence = cal.week(mon)
        wd = workdays(mon, absence)
        if len(wd) < 3:
            continue
        final, fgrp = _hours(res, set(wd), rules)
        adds, add_grp = [], Counter()
        for k in range(weeks):
            snap = datetime.combine(mon - timedelta(weeks=k), datetime.min.time())
            r_k, _ = cal.week(mon, created_before=snap)
            have, hgrp = _hours(r_k, set(wd), rules)
            adds.append(max(0.0, final - have) * 5 / len(wd))          # per full 5-day week
            if 1 <= k <= 4:
                add_grp.update({g: max(0.0, fgrp[g] - hgrp[g]) for g in fgrp})
        hist.append({"monday": mon, "final": final * 5 / len(wd), "groups": fgrp, "adds": adds, "add_groups": add_grp})
        for ev in cal.by_week.get(mon, []):
            known += 1
            if ev.get("created") and _p(ev["created"]) <= _p(ev["start"]):
                with_created += 1

    created_share = with_created / known if known else 0.0
    method = "lead_time" if hist and created_share >= 0.6 else "recent_weeks"
    finals = [h["final"] for h in hist]
    add_tot = Counter()
    for h in hist:
        add_tot.update(h["add_groups"] if method == "lead_time" else h["groups"])
    s = sum(add_tot.values()) or 1.0
    add_share = {g: v / s for g, v in add_tot.items() if v > 0}

    # ------------------------------------------------ the coming weeks
    out_weeks, slots = [], []
    for i in range(weeks):
        mon = this_mon + timedelta(weeks=i)
        res, absence = cal.week(mon)
        wd = workdays(mon, absence, from_day=today)
        full_wd = workdays(mon, absence)
        avail = len(wd) * nominal
        booked, bgrp = _hours(res, set(wd), rules)
        frac = len(wd) / 5
        if method == "lead_time":
            adds = [h["adds"][i] for h in hist]
            med, lo, hi = statistics.median(adds), _q(adds, 0.25), _q(adds, 0.75)
            med, lo, hi = med * frac, lo * frac, hi * frac
        else:
            base = [f * frac for f in finals] or [0.0]
            med = max(0.0, statistics.median(base) - booked)
            lo, hi = max(0.0, _q(base, 0.25) - booked), max(0.0, _q(base, 0.75) - booked)
        if not wd:
            med = lo = hi = 0.0
        expected = booked + med
        blocks = 0
        for d in wd:
            start_min = now.hour * 60 + now.minute if d == today else None
            fb = _free_blocks(res.get(d, []), w0, w1, start_min)
            blocks += len(fb)
            if (d - today).days < 14:
                slots += [{"date": d.isoformat(), "weekday": d.strftime("%a"), "from": _hm(a), "to": _hm(b),
                           "hours": round((b - a) / 60, 1)} for a, b in fb]
        ly_mon = date.fromisocalendar(mon.year - 1, min(mon.isocalendar()[1], 52), 1)
        ly_res, ly_abs = cal.week(ly_mon)
        ly_wd = workdays(ly_mon, ly_abs)
        last_year = round(_hours(ly_res, set(ly_wd), rules)[0], 1) if ly_wd and iso_week(ly_mon) not in vac else None
        out_weeks.append({
            "week": iso_week(mon), "monday": mon.isoformat(), "lead_weeks": i,
            "vacation": iso_week(mon) in vac, "workdays": len(wd), "workdays_total": len(full_wd),
            "holidays": [hol[mon + timedelta(days=k)] for k in range(5) if mon + timedelta(days=k) in hol],
            "available": round(avail, 1), "booked": round(booked, 1),
            "booked_by_group": {g: round(h, 1) for g, h in bgrp.items() if h > 0},
            "expected_additions": round(med, 1), "range": [round(booked + lo, 1), round(booked + hi, 1)],
            "expected_total": round(expected, 1), "expected_free": round(max(0.0, avail - expected), 1),
            "free_now": round(max(0.0, avail - booked), 1), "free_blocks": blocks,
            "expected_by_group": {g: round(med * sh, 1) for g, sh in add_share.items() if med * sh >= 0.1},
            "last_year": last_year,
        })

    notes = _notes(out_weeks, method, created_share, len(hist))
    return {"generated": now.strftime("%Y-%m-%dT%H:%M"), "method": method, "created_share": round(created_share, 2),
            "history_weeks": len(hist), "typical_week": round(statistics.median(finals), 1) if finals else 0,
            "weeks": out_weeks, "free_slots": slots, "notes": notes, "work_hours": [w0, w1],
            "addition_mix": {g: round(v, 3) for g, v in sorted(add_share.items(), key=lambda x: -x[1])}}


def _notes(ws, method, share, nhist):
    out = []
    live = [w for w in ws if w["workdays"] > 0]
    full = [w for w in live if w["available"] and w["expected_total"] >= FULL * w["available"]]
    for w in full[:3]:
        out.append(f"{w['week']} looks full: about {w['expected_total']:.0f} of {w['available']:.0f} available hours expected "
                   f"({w['booked']:.0f} h booked already).")
    cand = [w for w in live if w["lead_weeks"] > 0 and w["workdays"] >= 3]
    if cand:
        best = max(cand, key=lambda w: (w["expected_free"], w["free_blocks"]))
        out.append(f"Best week for focused work: {best['week']}, about {best['expected_free']:.0f} h expected to stay free "
                   f"({best['free_blocks']} free 2 h blocks at the moment).")
    for w in live:
        ly = w["last_year"]
        if ly is not None and ly - w["expected_total"] >= 8:
            out.append(f"{w['week']}: last year this week had {ly:.0f} h booked; the estimate now is {w['expected_total']:.0f} h. "
                       f"Something seasonal (teaching, deadlines) may still be missing.")
    vac = [w["week"] for w in ws if w["vacation"]]
    if vac:
        out.append("Vacation: " + ", ".join(vac) + ".")
    out.append("Estimate from how your calendar usually fills up in the weeks before (creation dates of past events, "
               f"{nhist} weeks of history)." if method == "lead_time" else
               "Estimate from your recent comparable weeks (event creation dates were not usable"
               f"{f', only {share * 100:.0f}% available' if share else ''}).")
    return out
