"""Calendar time statistics (KAN-54). Pure Python, no EventKit: harvest.py supplies the events.

Input events: {"title", "start", "end" ("YYYY-MM-DDTHH:MM" local), "all_day": bool,
               "avail": "busy" | "free" | "tentative" | "oof"}
Rules, groups and vacation weeks come from config.json -> "stats".

Classification: a known category name in square brackets at the end of a title ("Report [Projects]") decides;
then the event's Outlook category through category_map (v0.67, KAN-105: {"Focus Work": "Focus time"}, events
carry "ocat" when server.py could read Outlook); otherwise the first matching title rule.

Counting:
- each event is clipped to the working window (work_hours) day by day;
- overlapping events count once: the slot goes to the timed, accepted event that matches the earliest rule
  (all-day off-site blocks and tentative invitations only fill time nothing else claims);
- 'Absence' marks time off and is not counted, 'Ignore' is not counted, 'free' events are not counted;
- a work event marked 'out of office' in Exchange is an off-site day (out_of_office_as);
- all-day events of the off-site categories (conferences, travel) fill the working window on weekdays.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

UNGROUPED = "Not grouped"
UNCAT = "Uncategorized"
SKIP = ("Absence", "Ignore")
ALLDAY_CATS = ("Conferences", "Travel & delegations")
TAG_RE = re.compile(r"\[([^\[\]]+)\]\s*$")   # "Project report [Projects]": a chosen category (Plan blocks, KAN-61)


# ---------------------------------------------------------------- calendar helpers

def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def swedish_holidays(year: int) -> dict:
    """Public holidays plus the eves that are de facto days off (midsommar, jul, nyår)."""
    e = easter(year)
    midsummer_eve = next(date(year, 6, d) for d in range(19, 26) if date(year, 6, d).weekday() == 4)
    return {
        date(year, 1, 1): "Nyårsdagen", date(year, 1, 6): "Trettondedag jul",
        e - timedelta(days=2): "Långfredagen", e + timedelta(days=1): "Annandag påsk",
        date(year, 5, 1): "Första maj", e + timedelta(days=39): "Kristi himmelsfärd",
        date(year, 6, 6): "Nationaldagen", midsummer_eve: "Midsommarafton",
        date(year, 12, 24): "Julafton", date(year, 12, 25): "Juldagen",
        date(year, 12, 26): "Annandag jul", date(year, 12, 31): "Nyårsafton",
    }


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(key: str) -> date:
    y, w = key.split("-W")
    return date.fromisocalendar(int(y), int(w), 1)


def _p(s: str) -> datetime:
    s = s[:16]
    return datetime.strptime(s if "T" in s else s + "T00:00", "%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------- classification

class Rules:
    def __init__(self, st: dict):
        self.rules = [(g["group"], re.compile(g["match"], re.I)) for g in st.get("groups", [])]
        self.order = {}
        for i, (name, _) in enumerate(self.rules):
            self.order.setdefault(name, i)
        self.order.setdefault(UNCAT, len(self.rules))
        ooo = st.get("out_of_office_as") or {}
        self.ooo_group = ooo.get("group")
        self.ooo_except = set(ooo.get("except", [])) | set(SKIP)
        self.group_of = {c: g for g, cats in (st.get("supergroups") or {}).items() for c in cats}
        self.groups = list((st.get("supergroups") or {}).keys())
        self.names = {name.lower(): name for name, _ in self.rules if name not in SKIP}
        # v0.67 (KAN-105): Outlook category -> category; an empty target leaves the event to the title rules
        self.cmap = {str(k).strip().lower(): str(v).strip() for k, v in (st.get("category_map") or {}).items()
                     if not str(k).startswith("_") and str(v).strip()}
        for name in self.cmap.values():
            self.order.setdefault(name, len(self.rules))

    def choices(self) -> list:
        """Categories a title tag may name, in rule order."""
        return list(dict.fromkeys(self.names.values()))

    def tagged(self, title: str):
        m = TAG_RE.search(title or "")
        return self.names.get(m.group(1).strip().lower()) if m else None

    def mapped(self, ocat):
        """First of the event's Outlook categories that category_map knows, else None."""
        return next((self.cmap[c.lower()] for c in (ocat or []) if c.lower() in self.cmap), None)

    def category(self, title: str, avail: str = "busy", ocat=None) -> str:
        c = self.tagged(title) or self.mapped(ocat)
        if c:
            return c                      # chosen by hand, wins over the title rules and out of office
        c = next((name for name, rx in self.rules if rx.search(title or "")), UNCAT)
        if avail == "oof" and self.ooo_group and c not in self.ooo_except:
            c = self.ooo_group
        return c

    def group(self, cat: str) -> str:
        return self.group_of.get(cat, UNGROUPED)


# ---------------------------------------------------------------- core

def _slots(events, rules: Rules, start: date, end: date, w0: int, w1: int):
    """Per day: list of (start_min, end_min, priority, category, title, tentative), plus absence days."""
    days = defaultdict(list)
    absence = set()
    lo, hi = w0 * 60, w1 * 60
    for idx, ev in enumerate(events):
        title = (ev.get("title") or "").strip()
        avail = ev.get("avail") or "busy"
        cat = rules.category(title, avail, ev.get("ocat"))
        s, e = _p(ev["start"]), _p(ev["end"])
        if ev.get("all_day") and e.time() == datetime.min.time() and e > s:
            e -= timedelta(minutes=1)  # all-day end is midnight of the next day
        d = s.date()
        while d <= e.date():
            inside = start <= d <= end
            if cat == "Absence":
                if inside and d.weekday() < 5:
                    absence.add(d)
            elif inside and cat not in SKIP and avail != "free":
                if ev.get("all_day"):
                    if cat in ALLDAY_CATS and d.weekday() < 5:
                        days[d].append((lo, hi, (2, rules.order.get(cat, 99)), cat, title, False, idx))
                else:
                    a = max(lo, (s.hour * 60 + s.minute) if d == s.date() else 0)
                    b = min(hi, (e.hour * 60 + e.minute) if d == e.date() else 24 * 60)
                    if b > a:
                        tent = avail == "tentative"
                        days[d].append((a, b, (1 if tent else 0, rules.order.get(cat, 99)), cat, title, tent, idx))
            d += timedelta(days=1)
    return days, absence


def _resolve(day_items):
    """Overlaps count once. Returns list of (a, b, category, title, tentative, event index) without overlap."""
    cuts = sorted({x for it in day_items for x in it[:2]})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        live = [it for it in day_items if it[0] <= a and it[1] >= b]
        if not live:
            continue
        win = min(live, key=lambda it: it[2])
        if out and out[-1][1] == a and out[-1][2:] == (win[3], win[4], win[5], win[6]):
            out[-1] = (out[-1][0], b) + out[-1][2:]
        else:
            out.append((a, b, win[3], win[4], win[5], win[6]))
    return out


def compute(events, st: dict, start: date, end: date, vacation_weeks=None) -> dict:
    rules = Rules(st)
    w0, w1 = (st.get("work_hours") or [8, 18])
    nominal = float(st.get("nominal_hours_per_day", 8))
    vac = set(vacation_weeks if vacation_weeks is not None else st.get("vacation_weeks", []))
    days, absence = _slots(events, rules, start, end, w0, w1)

    hol = {}
    for y in range(start.year, end.year + 1):
        hol.update(swedish_holidays(y))

    cat_h, cat_t, cat_ev = Counter(), Counter(), defaultdict(set)
    unc = Counter()
    ung = Counter()                            # (category, title) -> hours for events outside every group (v0.57)
    week_cat = defaultdict(Counter)            # week -> category -> hours (Mon-Fri)
    heat = [[0.0] * (w1 - w0) for _ in range(5)]
    focus_blocks = 0
    work_days = 0
    for d, items in days.items():
        for a, b, cat, title, tent, idx in _resolve(items):
            h = (b - a) / 60
            cat_h[cat] += h
            cat_ev[cat].add(idx)
            if tent:
                cat_t[cat] += h
            if cat == UNCAT:
                unc[title] += h
            if rules.group(cat) == UNGROUPED:
                ung[(cat, title)] += h
            if d.weekday() < 5:
                week_cat[iso_week(d)][cat] += h
                if iso_week(d) not in vac:
                    for m in range(a, b):            # minute resolution into hour cells
                        heat[d.weekday()][m // 60 - w0] += 1 / 60

    # working days, available hours, focus blocks (free stretches of 2 h or more in the window)
    d = start
    while d <= end:
        wk = iso_week(d)
        if d.weekday() < 5 and wk not in vac and d not in hol and d not in absence:
            work_days += 1
            busy = sorted((a, b) for a, b, *_ in _resolve(days.get(d, [])))
            t = w0 * 60
            for a, b in busy + [(w1 * 60, w1 * 60)]:
                if a - t >= 120:
                    focus_blocks += 1
                t = max(t, b)
        d += timedelta(days=1)

    # weeks in the period (any week with at least one day inside)
    weeks, d = [], start - timedelta(days=start.weekday())
    while d <= end:
        weeks.append(iso_week(d))
        d += timedelta(days=7)
    work_weeks = [w for w in weeks if w not in vac]
    wk_hours = {w: round(sum(week_cat[w].values()), 2) for w in weeks}
    ww = [wk_hours[w] for w in work_weeks]
    total = sum(cat_h.values())
    booked_ww = sum(ww)
    available = work_days * nominal

    groups = rules.groups + [UNGROUPED]
    grp_h = Counter()
    for c, h in cat_h.items():
        grp_h[rules.group(c)] += h

    def q(xs, p):
        if not xs:
            return 0.0
        xs = sorted(xs)
        k = (len(xs) - 1) * p
        f = int(k)
        return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)

    nweeks = max(1, len(work_weeks))
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "work_hours": [w0, w1],
        "total_hours": round(total, 1),
        "tentative_hours": round(sum(cat_t.values()), 1),
        "categories": [
            {"name": c, "group": rules.group(c), "hours": round(h, 1), "tentative": round(cat_t[c], 1),
             "events": len(cat_ev[c]), "share": round(h / total, 4) if total else 0}
            for c, h in sorted(cat_h.items(), key=lambda x: -x[1])],
        "groups": [{"name": g, "hours": round(grp_h[g], 1), "share": round(grp_h[g] / total, 4) if total else 0,
                    "categories": [c for c in cat_h if rules.group(c) == g]}
                   for g in groups if grp_h[g] > 0 or g != UNGROUPED],
        "weeks": [{"week": w, "monday": week_monday(w).isoformat(), "hours": wk_hours[w], "vacation": w in vac,
                   "by_group": {g: round(sum(h for c, h in week_cat[w].items() if rules.group(c) == g), 2) for g in groups},
                   "holidays": [hol[dd] for dd in (week_monday(w) + timedelta(days=i) for i in range(5)) if dd in hol and start <= dd <= end]}
                  for w in weeks],
        "weekly": {"working_weeks": len(work_weeks), "vacation_weeks": len(weeks) - len(work_weeks),
                   "mean": round(statistics.mean(ww), 1) if ww else 0, "median": round(statistics.median(ww), 1) if ww else 0,
                   "p10": round(q(ww, 0.1), 1), "p90": round(q(ww, 0.9), 1)},
        "work_days": work_days,
        "available_hours": round(available, 1),
        "utilisation": round(booked_ww / available, 4) if available else 0,
        "focus_blocks_per_week": round(focus_blocks / nweeks, 1),
        "absence_days": len(absence),
        "suggested_vacation": sorted({iso_week(x) for x in absence
                                      if sum(1 for y in absence if iso_week(y) == iso_week(x)) >= 3} - vac),
        "heatmap": [[round(v / nweeks, 2) for v in row] for row in heat],
        "uncategorized": [{"title": t, "hours": round(h, 1)} for t, h in unc.most_common(15)],
        "ungrouped_titles": [{"title": t, "category": c, "hours": round(h, 2)} for (c, t), h in ung.most_common(80)],
    }


# ---------------------------------------------------------------- periods and comparison

def _minus_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return date(d.year - 1, 2, 28)


def period_range(kind: str, today: date, frm: str = "", to: str = ""):
    """Returns (start, end, previous_start, previous_end, label). Periods end yesterday at the latest."""
    y = today - timedelta(days=1)
    if kind == "last_week":
        s = today - timedelta(days=today.weekday() + 7); e = s + timedelta(days=6)
        return s, e, s - timedelta(days=7), s - timedelta(days=1), f"Week {s.isocalendar()[1]}"
    if kind == "last_month":
        e = today.replace(day=1) - timedelta(days=1); s = e.replace(day=1)
        pe = s - timedelta(days=1); ps = pe.replace(day=1)
        return s, e, ps, pe, s.strftime("%B %Y")
    if kind == "last_12m":
        s = _minus_year(y) + timedelta(days=1)
        return s, y, _minus_year(s), s - timedelta(days=1), "Last 12 months"
    if kind == "custom":
        s, e = date.fromisoformat(frm), date.fromisoformat(to)
        if e < s:
            s, e = e, s
        n = (e - s).days + 1
        return s, e, s - timedelta(days=n), s - timedelta(days=1), f"{s.isoformat()} to {e.isoformat()}"
    # this_year / calendar_year (default)
    return date(y.year, 1, 1), y, date(y.year - 1, 1, 1), _minus_year(y), f"{y.year} to date"


def summarise(cur: dict, prev: dict | None, label: str) -> list:
    """A few plain sentences, computed from the numbers."""
    out = []
    g = [x for x in cur["groups"] if x["name"] != UNGROUPED and x["hours"] > 0]
    if not g:
        return ["No booked time in this period."]
    top = sorted(g, key=lambda x: -x["hours"])
    w = cur["weekly"]
    out.append(f"{label}: {cur['total_hours']:.0f} booked hours over {w['working_weeks']} working week{'s' if w['working_weeks'] != 1 else ''} "
               f"(median {w['median']:.1f} h per week, utilisation {cur['utilisation'] * 100:.0f}% of available time).")
    lead = ", ".join(f"{x['name']} {x['share'] * 100:.0f}%" for x in top[:3])
    out.append(f"Largest groups: {lead}.")
    cats = [c for c in cur["categories"] if c["name"] != UNCAT]
    if cats:
        c = cats[0]
        out.append(f"Biggest single category: {c['name']} with {c['hours']:.0f} h ({c['share'] * 100:.0f}%).")
    if prev and prev["total_hours"] > 0:
        pg = {x["name"]: x["share"] for x in prev["groups"]}
        moves = sorted(((x["name"], (x["share"] - pg.get(x["name"], 0)) * 100) for x in g), key=lambda m: -abs(m[1]))
        moves = [m for m in moves if abs(m[1]) >= 3][:2]
        if moves:
            out.append("Compared with the previous period: " + "; ".join(
                f"{n} {'up' if dv > 0 else 'down'} {abs(dv):.0f} percentage points" for n, dv in moves) + ".")
        dt = cur["weekly"]["median"] - prev["weekly"]["median"]
        if abs(dt) >= 2:
            out.append(f"A typical working week is {abs(dt):.1f} h {'busier' if dt > 0 else 'lighter'} than before.")
    fb = cur["focus_blocks_per_week"]
    out.append(f"Free stretches of 2 h or more in the working day: {fb:.1f} per working week.")
    unc = next((c for c in cur["categories"] if c["name"] == UNCAT), None)
    if unc and unc["share"] >= 0.05:
        out.append(f"{unc['share'] * 100:.0f}% of booked time is uncategorized; see the list below to add rules.")
    return out
