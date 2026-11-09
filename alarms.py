"""Alarms (v0.43, KAN-84): Pomodoro-style countdowns with a description.

Kept on the server so the Mac, iPad and iPhone show the same countdowns. The server's background thread calls
tick() every second; an alarm whose time has come starts ringing, and the server raises a macOS notification
with sound (notify_mac), so it also works with no browser open. Pages ring as well (banner, beep).

State lives in data/alarms.json (or in memory for the demo); finished alarms are appended to data/alarm_log.jsonl
(label, minutes, when it rang), which gives the "done today" count. Nothing else is read or written.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

MAX_ACTIVE = 12
MAX_MINUTES = 12 * 60
RING_FORGET = 12 * 3600      # a ringing alarm nobody dismissed is dropped after 12 h


def _clean_label(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:120]


def _minutes(v) -> float:
    try:
        m = float(v)
    except (TypeError, ValueError):
        raise ValueError("Give the minutes")
    if not 0.1 <= m <= MAX_MINUTES:
        raise ValueError(f"Minutes must be between 0.1 and {MAX_MINUTES}")
    return round(m, 2)


def _at(hhmm: str, now: float) -> float:
    """'14:30' -> epoch of the next 14:30 (today, else tomorrow)."""
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", str(hhmm or "").strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError("Time must be HH:MM")
    base = datetime.fromtimestamp(now)
    t = base.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    if t.timestamp() <= now:
        t += timedelta(days=1)
    return t.timestamp()


class Alarms:
    def __init__(self, folder: Path | None = None):
        """folder: the data folder (alarms.json, alarm_log.jsonl); None keeps everything in memory (demo)."""
        self.folder = Path(folder).expanduser() if folder else None
        self.lock = threading.Lock()
        self._mem: list = []
        self._log: list = []

    # ---------------------------------------------------------------- storage
    def _load(self) -> list:
        if not self.folder:
            return [dict(a) for a in self._mem]
        try:
            return json.loads((self.folder / "alarms.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list):
        if not self.folder:
            self._mem = [dict(a) for a in items]
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / "alarms.json.tmp"
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.folder / "alarms.json")

    def _append_log(self, rec: dict):
        if not self.folder:
            self._log.append(rec)
            return
        with open(self.folder / "alarm_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def done_today(self, now: float | None = None) -> dict:
        """Alarms that rang today: count and minutes (a snooze ringing again does not count twice)."""
        day = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d")
        recs = self._log if not self.folder else []
        if self.folder:
            try:
                with open(self.folder / "alarm_log.jsonl", encoding="utf-8") as f:
                    recs = [json.loads(x) for x in f if day in x]
            except (OSError, ValueError):
                recs = []
        recs = [r for r in recs if str(r.get("rang", "")).startswith(day) and not r.get("snoozed")]
        return {"count": len(recs), "minutes": round(sum(float(r.get("minutes") or 0) for r in recs))}

    # ---------------------------------------------------------------- read
    def state(self, now: float | None = None) -> dict:
        now = now or time.time()
        with self.lock:
            items = self._load()
        items.sort(key=lambda a: (a["state"] != "ring", a.get("due") or now + a.get("left", 0)))
        return {"alarms": items, "now": now, "done_today": self.done_today(now)}

    # ---------------------------------------------------------------- write
    def act(self, body: dict, now: float | None = None) -> dict:
        now = now or time.time()
        act = body.get("action")
        with self.lock:
            items = self._load()
            if act == "add":
                if sum(a["state"] != "ring" for a in items) >= MAX_ACTIVE:
                    raise ValueError(f"At most {MAX_ACTIVE} alarms at once")
                label = _clean_label(body.get("label")) or "Alarm"
                if body.get("at"):
                    due = _at(body["at"], now)
                    minutes = round((due - now) / 60, 2)
                else:
                    minutes = _minutes(body.get("minutes"))
                    due = now + minutes * 60
                a = {"id": secrets.token_hex(4), "label": label, "minutes": minutes, "created": now,
                     "due": due, "state": "run", "snoozes": 0, "kind": str(body.get("kind") or "")[:20]}
                items.append(a)
                self._save(items)
                return dict(self._out(items, now), alarm=a)
            if act == "clear_rung":       # dismiss everything that rings
                items = [a for a in items if a["state"] != "ring"]
                self._save(items)
                return self._out(items, now)
            a = next((x for x in items if x["id"] == body.get("id")), None)
            if not a:
                raise ValueError("That alarm is gone (dismissed on another device?)")
            if act == "cancel" or act == "dismiss":
                items.remove(a)
            elif act == "pause":
                if a["state"] != "run":
                    raise ValueError("Only a running alarm can be paused")
                a.update(state="pause", left=max(0.0, a["due"] - now), due=None)
            elif act == "resume":
                if a["state"] != "pause":
                    raise ValueError("That alarm is not paused")
                a.update(state="run", due=now + a.pop("left", 0))
            elif act == "plus":         # +N minutes on a running or paused alarm
                extra = _minutes(body.get("minutes", 5)) * 60
                if a["state"] == "run":
                    a["due"] += extra
                elif a["state"] == "pause":
                    a["left"] = a.get("left", 0) + extra
                else:
                    raise ValueError("Use Snooze on a ringing alarm")
                a["minutes"] = round(a["minutes"] + extra / 60, 2)
            elif act == "snooze":
                if a["state"] != "ring":
                    raise ValueError("Only a ringing alarm can be snoozed")
                a.update(state="run", due=now + _minutes(body.get("minutes", 5)) * 60, snoozes=a.get("snoozes", 0) + 1)
                a.pop("rang", None)
            elif act == "again":        # same description and length, from now
                a.update(state="run", due=now + a["minutes"] * 60, created=now, snoozes=0)
                a.pop("rang", None); a.pop("left", None)
            else:
                raise ValueError("Unknown alarm action")
            self._save(items)
            return self._out(items, now)

    def _out(self, items, now):
        return {"alarms": sorted(items, key=lambda a: (a["state"] != "ring", a.get("due") or now + a.get("left", 0))),
                "now": now, "done_today": self.done_today(now)}

    def tick(self, now: float | None = None) -> list:
        """Called every second by the server. Returns the alarms that started ringing now."""
        now = now or time.time()
        rung = []
        with self.lock:
            items = self._load()
            changed = False
            for a in items:
                if a["state"] == "run" and a.get("due") and a["due"] <= now:
                    a.update(state="ring", rang=now)
                    rung.append(dict(a))
                    self._append_log({"label": a["label"], "minutes": a["minutes"], "kind": a.get("kind", ""),
                                      "rang": datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S"),
                                      "snoozed": a.get("snoozes", 0) > 0})
                    changed = True
            keep = [a for a in items if not (a["state"] == "ring" and now - a.get("rang", now) > RING_FORGET)]
            if changed or len(keep) != len(items):
                self._save(keep)
        return rung


# ---------------------------------------------------------------- the Mac side
NOTIFY = '''on run argv
	display notification (item 2 of argv) with title (item 1 of argv)
end run
'''


def notify_mac(alarm: dict, cfg: dict, log=print):
    """macOS notification plus a system sound. Values go in as osascript arguments, never into the script.
    The sound is played with afplay so it is heard even when notifications are muted for the app."""
    c = cfg.get("alarms") or {}
    if not c.get("mac_notify", True):
        return
    mins = alarm.get("minutes") or 0
    body = f"{alarm['label']} ({mins:g} min)" if mins else alarm["label"]
    try:
        subprocess.run(["osascript", "-", "WorkDashboard alarm", body], input=NOTIFY, capture_output=True, text=True, timeout=10)
    except Exception as e:  # noqa: BLE001  a missing notification must never stop the alarm loop
        log(f"alarm notification failed: {type(e).__name__}: {e}")
    sound = re.sub(r"[^A-Za-z0-9 _-]", "", str(c.get("mac_sound", "Glass")))
    path = Path("/System/Library/Sounds") / f"{sound}.aiff"
    if sound and path.exists():
        def play():
            for _ in range(max(1, min(10, int(c.get("sound_repeats", 3))))):
                subprocess.run(["afplay", str(path)], capture_output=True, timeout=15)
        threading.Thread(target=play, daemon=True).start()
