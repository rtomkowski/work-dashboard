"""WorkDashboard access token (KAN-42): second layer behind `tailscale serve`.

Every request needs a valid token, sent as the `wd_auth` cookie (browsers) or as
`Authorization: Bearer <token>` (scripts). A device enrols once by opening
`/?token=<token>`; the server sets a long-lived HttpOnly cookie and redirects to
the clean URL. The secret lives in the macOS login Keychain.

Server side:  import auth; auth.install(Handler)
CLI:          python auth.py show      links for this Mac and the iPad/iPhone (copies iPad link)
              python auth.py rotate    new token; every device must enrol again
              python auth.py link --local|--remote
              python auth.py status
Standard library only.
"""
from __future__ import annotations

import hmac
import html
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

SERVICE = "WorkDashboard-access"
ACCOUNT = "dashboard"
COOKIE = "wd_auth"
COOKIE_MAX_AGE = 400 * 24 * 3600   # browsers cap persistent cookies at ~400 days
CACHE_SECONDS = 60                 # a rotated token takes effect within a minute
MAX_FAILS, FAIL_WINDOW = 10, 300   # more than 10 bad tokens in 5 min -> 429
HERE = Path(__file__).resolve().parent
TAILSCALE_BINS = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")

_lock = threading.Lock()
_cache = {"token": None, "t": 0.0}
_fails: deque = deque()


# ---------- token storage (Keychain) ----------

def _security(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["security", *args], capture_output=True, text=True, timeout=10)


def keychain_read() -> str | None:
    r = _security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w")
    tok = r.stdout.strip()
    return tok if r.returncode == 0 and tok else None


def keychain_write(tok: str) -> None:
    r = _security("add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT,
                  "-l", "WorkDashboard access token", "-w", tok)
    if r.returncode != 0:
        raise RuntimeError(f"Keychain write failed: {r.stderr.strip() or r.returncode}")


def new_token() -> str:
    tok = secrets.token_urlsafe(32)  # 256 bits, URL safe
    keychain_write(tok)
    with _lock:
        _cache.update(token=tok, t=time.monotonic())
    return tok


def get_token(create: bool = True) -> str | None:
    """Cached Keychain read; creates the token on first use."""
    with _lock:
        if _cache["token"] and time.monotonic() - _cache["t"] < CACHE_SECONDS:
            return _cache["token"]
    try:
        tok = keychain_read()
    except Exception:
        tok = None
    if tok is None:
        with _lock:
            if _cache["token"]:          # Keychain hiccup: keep the last good token
                return _cache["token"]
        if not create:
            return None
        return new_token()
    with _lock:
        _cache.update(token=tok, t=time.monotonic())
    return tok


def valid(candidate: str | None) -> bool:
    if not candidate:
        return False
    tok = get_token()
    return bool(tok) and hmac.compare_digest(candidate.encode(), tok.encode())


# ---------- rate limit on bad tokens ----------

def _too_many_fails() -> bool:
    now = time.monotonic()
    with _lock:
        while _fails and now - _fails[0] > FAIL_WINDOW:
            _fails.popleft()
        return len(_fails) >= MAX_FAILS


def _record_fail() -> None:
    with _lock:
        _fails.append(time.monotonic())


# ---------- HTTP helpers ----------

def _cookie_token(h) -> str | None:
    raw = h.headers.get("Cookie")
    if not raw:
        return None
    try:
        c = SimpleCookie()
        c.load(raw)
        return c[COOKIE].value if COOKIE in c else None
    except Exception:
        return None


def _bearer(h) -> str | None:
    a = h.headers.get("Authorization", "")
    return a[7:].strip() if a.lower().startswith("bearer ") else None


def _is_https(h) -> bool:
    host = h.headers.get("Host", "").split(":")[0]
    return h.headers.get("X-Forwarded-Proto", "").lower() == "https" or host.endswith(".ts.net")


def _set_cookie(h, value: str, max_age: int) -> None:
    attrs = [f"{COOKIE}={value}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Lax"]
    if _is_https(h):
        attrs.append("Secure")
    h.send_header("Set-Cookie", "; ".join(attrs))


def _reply(h, code: int, body: bytes, ctype: str, extra=None) -> None:
    h.close_connection = True  # unread request bodies must not leak into keep-alive
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Cache-Control", "no-store")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Connection", "close")
    for k, v in (extra or []):
        h.send_header(k, v)
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(body)


def _redirect(h, location: str, cookie: str | None = None, clear: bool = False) -> None:
    h.close_connection = True
    h.send_response(303)
    h.send_header("Location", location)
    h.send_header("Cache-Control", "no-store")
    h.send_header("Referrer-Policy", "no-referrer")
    h.send_header("Content-Length", "0")
    h.send_header("Connection", "close")
    if cookie:
        _set_cookie(h, cookie, COOKIE_MAX_AGE)
    if clear:
        _set_cookie(h, "", 0)
    h.end_headers()


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkDashboard: sign in</title>
<style>
:root{{color-scheme:light dark;--bg:#f5f5f7;--card:#fff;--fg:#1d1d1f;--mute:#6e6e73;--acc:#0a66d8;--bd:#d2d2d7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1c1c1e;--fg:#f5f5f7;--mute:#a1a1a6;--acc:#4c9bff;--bd:#3a3a3c}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px;box-sizing:border-box}}
form{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:24px;width:min(380px,100%);box-sizing:border-box}}
h1{{font-size:20px;margin:0 0 6px}} p{{color:var(--mute);margin:0 0 16px;font-size:14px}}
input{{width:100%;box-sizing:border-box;font:inherit;padding:10px 12px;border-radius:9px;border:1px solid var(--bd);background:transparent;color:inherit}}
button{{margin-top:12px;width:100%;font:inherit;font-weight:600;padding:10px;border:0;border-radius:9px;background:var(--acc);color:#fff}}
.err{{color:#d70015;font-size:14px;margin-top:10px}}
</style></head><body>
<form method="post" action="/auth/login">
<h1>WorkDashboard</h1>
<p>This device is not enrolled. Paste the access token, or open the enrolment link from
<code>auth.py show</code> on the Mac.</p>
<input type="password" name="token" autocomplete="current-password" placeholder="Access token" autofocus required>
<input type="hidden" name="next" value="{next}">
<button type="submit">Sign in</button>{err}
</form></body></html>"""


def _deny(h, path: str, msg: str = "") -> None:
    wants_html = h.command in ("GET", "HEAD") and (
        path in ("/", "/index.html") or "text/html" in h.headers.get("Accept", ""))
    if wants_html:
        err = f'<div class="err">{html.escape(msg)}</div>' if msg else ""
        page = LOGIN_PAGE.format(next=html.escape(path), err=err).encode()
        return _reply(h, 401, page, "text/html; charset=utf-8")
    body = json.dumps({"error": "unauthorized", "hint": "enrol this device: python auth.py show"}).encode()
    _reply(h, 401, body, "application/json; charset=utf-8")


def _safe_next(nxt: str) -> str:
    # only same-site relative paths, never //evil or absolute URLs
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


def check(h) -> bool:
    """True if the request may proceed; otherwise a response has been sent."""
    parts = urlsplit(h.path)
    path, query = parts.path or "/", parse_qs(parts.query, keep_blank_values=True)

    if path == "/auth/logout":
        _redirect(h, "/", clear=True)
        return False

    if path == "/auth/login" and h.command == "POST":
        if _too_many_fails():
            _reply(h, 429, b"too many attempts, wait a few minutes", "text/plain")
            return False
        n = min(int(h.headers.get("Content-Length", 0) or 0), 4096)
        form = parse_qs(h.rfile.read(n).decode("utf-8", "replace")) if n else {}
        tok = (form.get("token") or [""])[0].strip()
        nxt = _safe_next((form.get("next") or ["/"])[0])
        if valid(tok):
            _redirect(h, nxt, cookie=tok)
        else:
            _record_fail()
            _deny(h, "/", "That token is not valid.")
        return False

    if "token" in query and h.command in ("GET", "HEAD"):
        if _too_many_fails():
            _reply(h, 429, b"too many attempts, wait a few minutes", "text/plain")
            return False
        tok = query.pop("token")[0].strip()
        clean = path + ("?" + urlencode(query, doseq=True) if query else "")
        if valid(tok):
            _redirect(h, clean, cookie=tok)
        else:
            _record_fail()
            _deny(h, path, "That enrolment link is not valid (token rotated?).")
        return False

    if valid(_cookie_token(h)) or valid(_bearer(h)):
        return True
    _deny(h, path)
    return False


def _guard(method):
    def wrapped(self, *a, **kw):
        try:
            ok = check(self)
        except Exception as e:  # fail closed
            try:
                _reply(self, 503, f"auth error: {e}".encode(), "text/plain")
            except Exception:
                pass
            return None
        if ok:
            return method(self, *a, **kw)
        return None
    wrapped.__wrapped__ = method
    wrapped._wd_auth = True
    return wrapped


def install(handler_cls) -> None:
    """Wrap every do_* method of the request handler with the token check."""
    for name in ("do_GET", "do_HEAD", "do_POST", "do_PUT", "do_PATCH", "do_DELETE"):
        m = getattr(handler_cls, name, None)
        if m is not None and not getattr(m, "_wd_auth", False):
            setattr(handler_cls, name, _guard(m))
    if getattr(handler_cls, "do_POST", None) is None:  # login form still needs POST
        def do_POST(self):
            self.send_error(404)
        handler_cls.do_POST = _guard(do_POST)
    get_token()  # create the Keychain entry at startup, not on the first request


# ---------- CLI ----------

def _port() -> int:
    for p in (HERE / "config.json",
              Path.home() / "Library/Application Support/WorkDashboard/config.json"):
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("port", 8765))
        except Exception:
            continue
    return 8765


def tailnet_host() -> str | None:
    for b in TAILSCALE_BINS:
        exe = shutil.which(b) or (b if Path(b).exists() else None)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
            name = json.loads(r.stdout)["Self"]["DNSName"].rstrip(".")
            if name:
                return name
        except Exception:
            continue
    return None


def link(kind: str, tok: str) -> str | None:
    if kind == "local":
        return f"http://127.0.0.1:{_port()}/?token={tok}"
    host = tailnet_host()
    return f"https://{host}/?token={tok}" if host else None


def _copy(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=5)
        return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    if cmd == "status":
        tok = get_token(create=False)
        print("Token in Keychain:", "yes" if tok else "no",
              f"(service '{SERVICE}', account '{ACCOUNT}')")
        print("Tailnet name:", tailnet_host() or "not found")
        return 0 if tok else 1
    if cmd == "link":
        kind = "remote" if "--remote" in argv else "local"
        url = link(kind, get_token())
        if not url:
            print("Tailscale name not found; is Tailscale running?", file=sys.stderr)
            return 1
        print(url)
        return 0
    if cmd in ("show", "rotate"):
        tok = new_token() if cmd == "rotate" else get_token()
        if cmd == "rotate":
            print("New token stored. Old cookies stop working within a minute; enrol each device again.\n")
        print("This Mac:     ", link("local", tok))
        remote = link("remote", tok)
        if remote:
            print("iPad / iPhone:", remote)
            if _copy(remote):
                print("\niPad link copied. Paste it into Safari on the iPad (Universal Clipboard), once.")
        else:
            print("iPad / iPhone: Tailscale name not found; use https://<mac>.<tailnet>.ts.net/?token=" + tok)
        print("\nToken (for the sign-in page):", tok)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
