"""Microsoft Outlook bridge for Plan blocks (KAN-61, v0.20): Outlook categories, which EventKit cannot see or set.

Talks to Outlook through AppleScript (osascript). Values go in as script arguments, never pasted into the script,
so titles and notes cannot change what runs. macOS asks once whether WorkDashboard may control Microsoft Outlook.
Writes are limited to creating Plan blocks and removing blocks of the dashboard's own last batch (tag in the notes).
"""
from __future__ import annotations

import html
import subprocess
import time

TIMEOUT = 45

_CATEGORIES = '''
on run argv
	set out to {}
	tell application "Microsoft Outlook"
		repeat with c in every category
			set {r, g, b} to color of c
			set end of out to ((id of c) as string) & tab & (name of c) & tab & r & "," & g & "," & b
		end repeat
	end tell
	set AppleScript's text item delimiters to linefeed
	return out as string
end run
'''

_CREATE = '''
on run argv
	set {acctName, calName, subj, bodyHtml, y, mo, dd, hh, mi, durMin, catName} to argv
	tell application "Microsoft Outlook"
		if acctName is "" then
			set acc to item 1 of (every exchange account)
		else
			set acc to exchange account acctName
		end if
		set cal to missing value
		repeat with c in (every calendar whose name is calName)
			if account of c is acc then
				set cal to c
				exit repeat
			end if
		end repeat
		if cal is missing value then error "No calendar '" & calName & "' in the Outlook account"
		set d1 to my mkdate(y, mo, dd, hh, mi)
		set d2 to d1 + (durMin as integer) * minutes
		set ev to make new calendar event at cal with properties {subject:subj, start time:d1, end time:d2, content:bodyHtml, free busy status:busy}
		set catDone to "none"
		if catName is not "" then
			try
				set category of ev to {first category whose name is catName}
				set catDone to "ok"
			on error
				set catDone to "failed"
			end try
		end if
		return ((id of ev) as string) & tab & catDone & tab & (name of acc)
	end tell
end run

on mkdate(y, mo, dd, hh, mi)
	set d to current date
	set day of d to 1
	set year of d to y as integer
	set month of d to mo as integer
	set day of d to dd as integer
	set hours of d to hh as integer
	set minutes of d to mi as integer
	set seconds of d to 0
	return d
end mkdate
'''

_REMOVE = '''
on run argv
	set tagText to item 1 of argv
	set out to {}
	tell application "Microsoft Outlook"
		repeat with i from 2 to count of argv
			set eid to item i of argv
			try
				set ev to calendar event id (eid as integer)
				if (content of ev) contains tagText then
					delete ev
					set end of out to "removed" & tab & eid
				else
					set end of out to "skipped" & tab & eid & tab & "not created by the dashboard"
				end if
			on error
				set end of out to "skipped" & tab & eid & tab & "already gone"
			end try
		end repeat
	end tell
	set AppleScript's text item delimiters to linefeed
	return out as string
end run
'''


def _run(script: str, args: list, timeout: int = TIMEOUT) -> str:
    r = subprocess.run(["osascript", "-", *[str(a) for a in args]], input=script, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1743" in err:
            raise PermissionError("WorkDashboard may not control Microsoft Outlook "
                                  "(System Settings > Privacy & Security > Automation)")
        raise RuntimeError(err[-300:] or f"osascript exit {r.returncode}")
    return r.stdout.rstrip("\n")


_cache = {"t": 0.0, "cats": None}


def categories(only=None, ttl: int = 600, with_id: bool = False) -> list:
    """[{name, color "#rrggbb"}] from Outlook, limited to and ordered like `only` when given. Cached for ttl s.
    with_id: every category with its Outlook id as well (for event_categories)."""
    if _cache["cats"] is None or time.time() - _cache["t"] > ttl:
        cats = []
        for line in _run(_CATEGORIES, []).splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            rgb = [int(x) // 257 for x in parts[2].split(",")]
            cats.append({"id": parts[0], "name": parts[1], "color": "#%02x%02x%02x" % tuple(rgb)})
        _cache.update(t=time.time(), cats=cats)
    cats = _cache["cats"]
    if with_id:
        return [dict(c) for c in cats]
    if only:
        by = {c["name"].lower(): c for c in cats}
        return [{"name": by[n.lower()]["name"], "color": by[n.lower()]["color"]} for n in only if n.lower() in by]
    return [{"name": c["name"], "color": c["color"]} for c in cats]


# v0.67 (KAN-105): read-only. Categories of the events in [from, to) of one Outlook calendar, plus every
# recurring series (Outlook gives only the series, not each occurrence). Bulk reads first; one event at a time
# if Outlook refuses the bulk form. Only events with a category are returned.
_EVCATS = '''
on run argv
	set {acctName, calName, y1, m1, d1, y2, m2, d2} to argv
	tell application "Microsoft Outlook"
		if acctName is "" then
			set acc to item 1 of (every exchange account)
		else
			set acc to exchange account acctName
		end if
		set cal to missing value
		repeat with c in (every calendar whose name is calName)
			if account of c is acc then
				set cal to c
				exit repeat
			end if
		end repeat
		if cal is missing value then error "No calendar '" & calName & "' in the Outlook account"
	end tell
	set dA to my mkdate(y1, m1, d1, 0, 0)
	set dB to my mkdate(y2, m2, d2, 0, 0)
	set out to {}
	tell application "Microsoft Outlook"
		set refs to (a reference to (every calendar event of cal whose start time is greater than or equal to dA and start time is less than dB))
		set out to out & my readAll(refs, "0")
		try
			set rrefs to (a reference to (every calendar event of cal whose is recurring is true))
			set out to out & my readAll(rrefs, "1")
		end try
	end tell
	set AppleScript's text item delimiters to linefeed
	return out as string
end run

on readAll(refs, rec)
	set out to {}
	tell application "Microsoft Outlook"
		try
			set cats to category of refs
			set subs to subject of refs
			set starts to start time of refs
		on error
			set cats to {}
			set subs to {}
			set starts to {}
			repeat with ev in (contents of refs)
				set end of cats to category of ev
				set end of subs to subject of ev
				set end of starts to start time of ev
			end repeat
		end try
		repeat with i from 1 to count of cats
			set cs to item i of cats
			if cs is not {} and cs is not missing value then
				set ids to {}
				repeat with c in cs
					set end of ids to (id of c) as string
				end repeat
				set AppleScript's text item delimiters to ","
				set idText to ids as string
				set AppleScript's text item delimiters to ""
				set s to item i of subs
				if s is missing value then set s to ""
				set end of out to my fmt(item i of starts) & tab & rec & tab & idText & tab & s
			end if
		end repeat
	end tell
	return out
end readAll

on fmt(d)
	return (year of d as string) & "-" & my pad((month of d) as integer) & "-" & my pad(day of d) & "T" & my pad(hours of d) & ":" & my pad(minutes of d)
end fmt

on pad(n)
	return text -2 thru -1 of ("0" & (n as string))
end pad

on mkdate(y, mo, dd, hh, mi)
	set d to current date
	set day of d to 1
	set year of d to y as integer
	set month of d to mo as integer
	set day of d to dd as integer
	set hours of d to hh as integer
	set minutes of d to mi as integer
	set seconds of d to 0
	return d
end mkdate
'''

_evcache = {}            # (account, calendar, from, to) -> (time, index)


def event_categories(start, end, account: str = "", calendar: str = "Calendar", ttl: int = 900) -> dict:
    """Outlook categories of events starting in [start, end) (dates), read-only, cached for ttl s.
    Returns {"at": {(title lower, "YYYY-MM-DDTHH:MM"): [names]}, "series": {title lower: [names]}}."""
    key = (account, calendar, start.isoformat(), end.isoformat())
    hit = _evcache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    names = {c["id"]: c["name"] for c in categories(ttl=ttl, with_id=True)}
    at, series = {}, {}
    out = _run(_EVCATS, [account, calendar, start.year, start.month, start.day, end.year, end.month, end.day],
               timeout=180)
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        when, rec, ids, subj = parts
        cats = [names[i] for i in ids.split(",") if i in names]
        if not cats:
            continue
        k = subj.strip().lower()
        if rec == "1":
            series.setdefault(k, cats)
        else:
            at[(k, when)] = cats
    idx = {"at": at, "series": series}
    for k in [k for k, v in _evcache.items() if time.time() - v[0] >= ttl]:
        _evcache.pop(k, None)
    _evcache[key] = (time.time(), idx)
    return idx


def tag_events(events: list, idx: dict) -> int:
    """Puts "ocat" (Outlook category names) on events that match by title and start; a recurring
    occurrence ("rec") falls back to its series by title. Returns how many got one."""
    n = 0
    for ev in events:
        k = (ev.get("title") or "").strip().lower()
        cats = idx["at"].get((k, ev.get("start")))
        if cats is None and ev.get("rec"):
            cats = idx["series"].get(k)
        if cats:
            ev["ocat"] = cats
            n += 1
    return n


def notes_html(notes: str, tag: str) -> str:
    """Event body as Outlook stores it (HTML): your notes, then the undo tag on its own line."""
    parts = [html.escape(p) for p in ([notes] if notes else []) + [tag]]
    return "<br><br>".join(p.replace("\n", "<br>") for p in parts)


def create_events(blocks: list, account: str, calendar: str, tag: str) -> dict:
    """blocks: [{title, notes, start, end, category}]. One call per block; a failure is reported per row."""
    created, errors, warnings, acct = [], [], [], ""
    for i, b in enumerate(blocks):
        try:
            y, mo, dd = b["start"][:10].split("-")
            hh, mi = b["start"][11:16].split(":")
            dur = (int(b["end"][11:13]) * 60 + int(b["end"][14:16])) - (int(hh) * 60 + int(mi))
            out = _run(_CREATE, [account, calendar, b["title"], notes_html(b.get("notes", ""), tag),
                                 y, mo, dd, hh, mi, dur, b.get("category") or ""]).split("\t")
            eid, cat, acct = out[0], out[1] if len(out) > 1 else "none", out[2] if len(out) > 2 else acct
            if cat == "failed":
                warnings.append({"i": i, "warning": f"category '{b['category']}' could not be set"})
            created.append({"i": i, "id": eid, "title": b["title"], "start": b["start"], "end": b["end"],
                            "category": b.get("category") if cat == "ok" else ""})
        except Exception as e:
            errors.append({"i": i, "error": f"{type(e).__name__}: {e}"})
    return {"calendar": f"{calendar} (Outlook)", "account": acct, "created": created, "errors": errors,
            "warnings": warnings}


def remove_events(ids: list, tag: str) -> dict:
    removed, skipped = [], []
    for line in _run(_REMOVE, [tag, *ids]).splitlines():
        parts = line.split("\t")
        if parts[0] == "removed":
            removed.append(parts[1])
        elif len(parts) >= 3:
            skipped.append({"id": parts[1], "why": parts[2]})
    return {"removed": removed, "skipped": skipped}
