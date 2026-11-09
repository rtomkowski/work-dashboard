"""New Obsidian notes from the dashboard (v0.37, KAN-68; weekly review export KAN-70).

Create-only: a note is written into a whitelisted vault folder with O_EXCL, so an existing file is
never touched; a clash gets " 2", " 3", ... appended. Nothing here edits, moves or deletes a note.

Whitelist: obsidian.write_folders in config.json, or the folders the dashboard already reads
(obsidian.folders) when that list is empty; any subfolder of a whitelisted folder is allowed too.
Templates: the built-ins below, plus every .md file in obsidian.templates_folder ("Templates");
{{title}}, {{date}} and {{time}} are filled in, as Obsidian's own Templates plugin does.
"""
from __future__ import annotations

import fnmatch
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

MAX_TITLE = 120
MAX_CONTENT = 200_000
WORD = re.compile(r"[A-Za-z][\w-]*")
BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def vault(cfg: dict) -> Path:
    return Path(cfg["obsidian"]["vault_path"]).expanduser()


def roots(cfg: dict) -> list:
    o = cfg["obsidian"]
    out = [str(f).strip().strip("/") for f in (o.get("write_folders") or o.get("folders") or []) if str(f).strip()]
    return list(dict.fromkeys(out))


def _hidden_or_excluded(rel: str, excl: list) -> bool:
    return any(p.startswith(".") for p in rel.split("/")) or any(fnmatch.fnmatch(rel + "/x.md", pat) for pat in excl)


def targets(cfg: dict, max_depth: int = 2) -> list:
    """Whitelisted folders plus their subfolders (max_depth levels below each), as vault-relative paths."""
    v, excl, out = vault(cfg), cfg["obsidian"].get("exclude", []), []
    for r in roots(cfg):
        base = v / r
        if not base.is_dir():
            continue
        out.append(r)
        for dirpath, dirnames, _ in os.walk(base):
            rel = Path(dirpath).relative_to(v).as_posix()
            depth = rel.count("/") - r.count("/")
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            if depth >= max_depth:
                dirnames[:] = []
            if rel != r and not _hidden_or_excluded(rel, excl):
                out.append(rel)
    return list(dict.fromkeys(out))


def default_folder(cfg: dict, kind: str = "note") -> str:
    o, ts = cfg["obsidian"], targets(cfg)
    want = (o.get({"meeting": "meeting_folder", "review": "review_folder"}.get(kind, "note_folder")) or "").strip("/")
    for w in (want, (o.get("note_folder") or "").strip("/")):
        if w and w in ts:
            return w
    return ts[0] if ts else ""


def check_folder(cfg: dict, folder: str) -> Path:
    """The absolute folder, only if it is a whitelisted folder or below one, inside the vault."""
    folder = str(folder or "").strip().strip("/")
    v = vault(cfg).resolve()
    if not folder or ".." in folder.split("/") or any(p.startswith(".") for p in folder.split("/")):
        raise ValueError("Pick a folder from the list")
    if not any(folder == r or folder.startswith(r + "/") for r in roots(cfg)):
        raise ValueError(f"'{folder}' is not a folder the dashboard may write to (obsidian.write_folders)")
    p = (v / folder).resolve()
    if v not in p.parents:
        raise ValueError("Folder outside the vault")
    if not p.is_dir():
        raise ValueError(f"Folder not found: {folder}")
    return p


def safe_name(title: str) -> str:
    s = BAD_CHARS.sub(" ", str(title or ""))
    s = re.sub(r"\s+", " ", s).strip().strip(".").strip()
    return s[:MAX_TITLE].rstrip() or "Untitled"


# ---------------------------------------------------------------- templates

def _yq(s) -> str:
    """A YAML double-quoted scalar."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _fm(pairs: list) -> str:
    out = ["---"]
    for k, v in pairs:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            out.append(f"{k}:")
            out += ["  - " + (str(x) if WORD.fullmatch(str(x)) else _yq(x)) for x in v]
        else:
            plain = isinstance(v, (int, float)) or re.fullmatch(r"[\w./:-]+", str(v))
            out.append(f"{k}: {v if plain else _yq(v)}")
    return "\n".join(out + ["---", ""])


def _when(ev: dict) -> str:
    s = datetime.fromisoformat(ev["start"])
    e = datetime.fromisoformat(ev["end"]) if ev.get("end") else s
    day = s.strftime("%a %-d %b %Y")
    return f"{day}, all day" if ev.get("all_day") else f"{day}, {s:%H:%M} to {e:%H:%M}"


def meeting_text(title: str, now: datetime, ev: dict | None) -> str:
    ev = ev or {}
    s = datetime.fromisoformat(ev["start"]) if ev.get("start") else now
    e = datetime.fromisoformat(ev["end"]) if ev.get("end") else None
    people = [a.get("name") or a.get("email") for a in ev.get("attendees") or [] if not a.get("me")]
    people = [p for p in people if p]
    org = ev.get("organizer") or {}
    fm = _fm([("date", s.strftime("%Y-%m-%d")),
              ("time", "" if ev.get("all_day") or not e else f"{s:%H:%M}-{e:%H:%M}"),
              ("type", "meeting"), ("event", ev.get("title", "")), ("calendar", ev.get("calendar", "")),
              ("location", ev.get("location", "")),
              ("organizer", "" if org.get("me") else org.get("name", "")),
              ("attendees", people[:40]), ("tags", ["meeting"])])
    lines = [fm, f"# {title}", ""]
    if ev:
        lines.append(f"**When:** {_when(ev)}  ")
        if ev.get("location"):
            lines.append(f"**Where:** {ev['location']}  ")
        if people:
            lines.append(f"**With:** {', '.join(people[:40])}{' and others' if len(people) > 40 else ''}  ")
        lines.append("")
    lines += ["## Agenda", "- ", "", "## Notes", "", "", "## Decisions", "- ", "", "## Actions", "- [ ] ", ""]
    return "\n".join(lines)


def idea_text(title: str, now: datetime, ev=None) -> str:
    return "\n".join([_fm([("date", now.strftime("%Y-%m-%d")), ("type", "idea"), ("tags", ["idea"])]),
                      f"# {title}", "", "## The idea", "", "", "## Why it matters", "", "", "## Next step", "- ", ""])


def literature_text(title: str, now: datetime, ev=None) -> str:
    fm = "\n".join(["---", f"date: {now:%Y-%m-%d}", "type: literature", 'authors: ""', 'year: ""', 'doi: ""',
                    'source: ""', "tags:", '  - "literature"', "---", ""])
    return "\n".join([fm, f"# {title}", "", "## Summary", "", "", "## Key findings", "- ", "",
                      "## Methods", "", "", "## Relevance to my work", "", "", "## Quotes and figures", "- ", ""])


def blank_text(title: str, now: datetime, ev=None) -> str:
    return "\n".join([_fm([("date", now.strftime("%Y-%m-%d"))]), f"# {title}", "", ""])


BUILTIN = {"meeting": ("Meeting note", meeting_text), "idea": ("Idea", idea_text),
           "literature": ("Literature note", literature_text), "blank": ("Blank", blank_text)}


def user_templates(cfg: dict) -> dict:
    """name -> path for the .md files in obsidian.templates_folder (top level, at most 30)."""
    tf = (cfg["obsidian"].get("templates_folder") or "").strip("/")
    d = vault(cfg) / tf if tf else None
    if not d or ".." in tf.split("/") or not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.glob("*.md"))[:30] if not p.name.startswith(".")}


def template_list(cfg: dict) -> list:
    out = [{"id": k, "name": v[0], "builtin": True} for k, v in BUILTIN.items()]
    out += [{"id": "user:" + k, "name": k, "builtin": False} for k in user_templates(cfg)]
    return out


def with_meeting(text: str, title: str) -> str:
    """v0.54 (KAN-96): a note made from an event (any template but the meeting one, which has event:) gets
    meeting: <event title> in its frontmatter, so the dashboard lists it under that meeting."""
    if not title:
        return text
    line = f"meeting: {_yq(title)}"
    m = re.match(r"^---[ \t]*\n(.*?\n)?---[ \t]*(\n|$)", text, re.S)
    if not m:
        return f"---\n{line}\n---\n\n{text}"
    fm = m.group(1) or ""
    if re.search(r"^(meeting|meetings|event):", fm, re.M | re.I):
        return text
    return f"---\n{fm}{line}\n---" + text[m.end(0) - len(m.group(2)):]


def render(cfg: dict, template: str, title: str, now: datetime, ev: dict | None) -> str:
    if template in BUILTIN:
        return BUILTIN[template][1](title, now, ev)
    if template.startswith("user:"):
        p = user_templates(cfg).get(template[5:])
        if p is None:
            raise ValueError("Template not found")
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for k, v in (("title", title), ("date", now.strftime("%Y-%m-%d")), ("time", now.strftime("%H:%M"))):
            txt = re.sub(r"\{\{\s*" + k + r"\s*\}\}", lambda m, v=v: v, txt)
        return txt
    raise ValueError("Unknown template")


# ---------------------------------------------------------------- create

def uri(cfg: dict, rel: str) -> str:
    o = cfg["obsidian"]
    name = o.get("vault_name") or vault(cfg).name
    return "obsidian://open?" + urllib.parse.urlencode({"vault": name, "file": rel[:-3] if rel.endswith(".md") else rel},
                                                       quote_via=urllib.parse.quote)


def create(cfg: dict, folder: str, title: str, template: str = "blank", content: str | None = None,
           event: dict | None = None, now: datetime | None = None) -> dict:
    """Write a new note; never overwrites. Returns {name, path (vault-relative), uri, renamed}."""
    now = now or datetime.now()
    d = check_folder(cfg, folder)
    base = safe_name(title)
    text = content if content is not None else render(cfg, template, base, now, event)
    if event and (content is not None or template != "meeting"):
        text = with_meeting(text, event.get("title", ""))
    if len(text) > MAX_CONTENT:
        raise ValueError("Note too long")
    for i in range(1, 51):
        name = base if i == 1 else f"{base} {i}"
        p = d / (name + ".md")
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        rel = p.relative_to(vault(cfg).resolve()).as_posix()
        return {"name": name, "path": rel, "uri": uri(cfg, rel), "renamed": i > 1}
    raise ValueError("Too many notes with this name")


def existing(cfg: dict, folder: str, title: str) -> str | None:
    """Vault-relative path if a note with exactly this name is already in the folder."""
    try:
        p = check_folder(cfg, folder) / (safe_name(title) + ".md")
    except ValueError:
        return None
    return p.relative_to(vault(cfg).resolve()).as_posix() if p.exists() else None


# ---------------------------------------------------------------- quick capture (v0.42, KAN-72; two files v0.44)
# Append-only: one line is added at the end of one capture file. Nothing already in the file is rewritten.
# Text goes to capture_text_file, tasks to capture_task_file (v0.44); capture_file is the old single-file key,
# used only when the new keys are empty. capture_daily_folder, when set, still takes both.

def capture_path(cfg: dict, now: datetime, as_task: bool = False) -> Path:
    o = cfg["obsidian"]
    daily = (o.get("capture_daily_folder") or "").strip("/")
    one = o.get("capture_task_file" if as_task else "capture_text_file") or o.get("capture_file") or "Inbox/Capture.md"
    rel = f"{daily}/{now:%Y-%m-%d}.md" if daily else str(one).strip().strip("/")
    if not rel.endswith(".md"):
        rel += ".md"
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    d = check_folder(cfg, parent)              # the capture file must sit in a folder the dashboard may write to
    name = rel.rsplit("/", 1)[-1]
    if name.startswith(".") or "/" in name:
        raise ValueError("Bad capture file name")
    return d / name


def capture(cfg: dict, text: str, as_task: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    line = re.sub(r"\s+", " ", str(text or "")).strip()[:500]
    if not line:
        raise ValueError("Nothing to capture")
    p = capture_path(cfg, now, as_task)
    new = not p.exists()
    entry = f"- [ ] {line}" if as_task else f"- {now:%Y-%m-%d %H:%M} {line}"
    with open(p, "a+", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(f.tell() - 1)
            last = f.read(1)
            f.seek(0, os.SEEK_END)
            if last != "\n":
                f.write("\n")
        else:
            f.write(f"# {p.stem}\n\n")
        f.write(entry + "\n")
    rel = p.relative_to(vault(cfg).resolve()).as_posix()
    return {"path": rel, "uri": uri(cfg, rel), "line": entry, "created": new}


# ---------------------------------------------------------------- Inbox triage (v0.42, KAN-78)
# Notes can only be moved OUT of the Inbox folder, into a whitelisted folder, and never onto an existing file.

def inbox_dir(cfg: dict) -> Path:
    return check_folder(cfg, cfg["obsidian"].get("inbox_folder") or "Inbox")


def _first_text(txt: str, n: int = 160) -> str:
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", txt, flags=re.S)
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith(("#", "```", "|", "![["))]
    t = re.sub(r"\[\[([^\]|]+)\|?([^\]]*)\]\]", lambda m: m.group(2) or m.group(1), " ".join(lines))
    t = re.sub(r"[*_`]", "", t)
    return t[:n] + ("…" if len(t) > n else "")


def inbox_list(cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    d = inbox_dir(cfg)
    out = []
    caps = set()
    for t in (False, True):
        try:
            caps.add(capture_path(cfg, now, t).resolve())
        except ValueError:
            pass
    for p in sorted(d.glob("*.md")):
        if p.name.startswith(".") or p.resolve() in caps:
            continue
        try:
            st = p.stat()
            txt = p.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            continue
        born = datetime.fromtimestamp(min(getattr(st, "st_birthtime", st.st_ctime), st.st_mtime))
        rel = p.relative_to(vault(cfg).resolve()).as_posix()
        out.append({"title": p.stem, "path": rel, "uri": uri(cfg, rel), "created": born.strftime("%Y-%m-%dT%H:%M"),
                    "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M"),
                    "age_days": (now - born).days, "excerpt": _first_text(txt)})
    out.sort(key=lambda n: n["created"])
    folder = d.relative_to(vault(cfg).resolve()).as_posix()
    return {"folder": folder, "notes": out, "targets": [t for t in targets(cfg) if t != folder]}


def _rename_new(src: Path, dst: Path):
    """Rename that never replaces a file: checked right before, under the server's lock. (Hard links, which
    would make it atomic, are not reliable inside OneDrive's cloud folder.)"""
    if dst.exists():
        raise ValueError(f"{dst.name} already exists there; nothing was moved")
    os.rename(src, dst)


def move_note(cfg: dict, rel: str, folder: str) -> dict:
    v = vault(cfg).resolve()
    src = (v / str(rel or "")).resolve()
    inbox = inbox_dir(cfg)
    if src.parent != inbox or src.suffix != ".md" or not src.is_file():
        raise ValueError("Only notes directly in the Inbox folder can be moved")
    dst_dir = check_folder(cfg, folder)
    if dst_dir == inbox:
        raise ValueError("That is the Inbox itself")
    dst = dst_dir / src.name
    if dst.exists():
        raise ValueError(f"{folder} already has a note called {src.stem}; rename one of them in Obsidian first")
    _rename_new(src, dst)
    return {"from": src.relative_to(v).as_posix(), "to": dst.relative_to(v).as_posix(),
            "uri": uri(cfg, dst.relative_to(v).as_posix())}


def unmove_note(cfg: dict, move: dict) -> dict:
    v = vault(cfg).resolve()
    src, back = (v / move["to"]).resolve(), (v / move["from"]).resolve()
    if v not in src.parents or v not in back.parents or not src.is_file():
        raise ValueError("The moved note is no longer where it was put")
    if back.exists():
        raise ValueError("A note with that name is back in the Inbox; nothing was moved")
    _rename_new(src, back)
    return {"from": move["to"], "to": move["from"]}
