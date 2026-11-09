#!/bin/bash
# Work Dashboard installer for macOS.
#
#   git clone <this repository> && cd <folder> && ./install.sh
#
# Asks a few questions (your name, Obsidian, Jira, Outlook, weather, iPhone/iPad access),
# writes ~/Library/Application Support/WorkDashboard/config.json, installs a private Python
# environment, and starts the dashboard at login. Safe to re-run: it offers to keep your settings.
#
# Options:
#   --defaults            no questions: no Obsidian, no Jira, no Outlook, no weather
#   --config-only FILE    only ask the questions and write the config to FILE (nothing is installed)
#   --help
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/WorkDashboard"
TTY="${WD_TTY:-/dev/tty}"          # answers come from the terminal, also when the script is piped
DEFAULTS=0
CONFIG_ONLY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --defaults) DEFAULTS=1 ;;
    --config-only) CONFIG_ONLY="${2:?--config-only needs a file}"; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)"; exit 2 ;;
  esac
  shift
done

# open the terminal once on fd 3 (each prompt reads the next line from it)
if [ "$DEFAULTS" = 0 ]; then
  exec 3< "$TTY" || { echo "No terminal for questions: run ./install.sh in Terminal, or use --defaults."; exit 1; }
fi

# ---------- small helpers ----------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }

# ask "Question" "default"  -> prints the answer
ask() {
  local q="$1" def="${2:-}" a=""
  if [ "$DEFAULTS" = 1 ]; then printf '%s' "$def"; return; fi
  if [ -n "$def" ]; then printf '%s [%s]: ' "$q" "$def" >&2; else printf '%s: ' "$q" >&2; fi
  IFS= read -r -u 3 a || true
  printf '%s' "${a:-$def}"
}
# ask_yn "Question" y|n  -> exit status 0 for yes
ask_yn() {
  local q="$1" def="$2" a hint="[y/N]"
  [ "$def" = y ] && hint="[Y/n]"
  if [ "$DEFAULTS" = 1 ]; then [ "$def" = y ]; return; fi
  while true; do
    printf '%s %s: ' "$q" "$hint" >&2
    IFS= read -r -u 3 a || return 1
    a="$(printf '%s' "${a:-$def}" | tr '[:upper:]' '[:lower:]')"
    case "$a" in y|yes) return 0 ;; n|no) return 1 ;; esac
  done
}
ask_secret() {
  local q="$1" a=""
  printf '%s (typing is hidden): ' "$q" >&2
  stty -echo <&3 2>/dev/null || true
  IFS= read -r -u 3 a || true
  stty echo <&3 2>/dev/null || true
  printf '\n' >&2
  printf '%s' "$a"
}

# ---------- 1. requirements ----------
step "Checking requirements"
[ "$(uname -s)" = Darwin ] || die "Work Dashboard runs on macOS only."
MACOS="$(sw_vers -productVersion)"
case "$MACOS" in 1[3-9].*|2[0-9].*) ok "macOS $MACOS" ;; *) warn "macOS $MACOS: tested on macOS 13 or later; older versions may not grant Calendar access." ;; esac
[ -f "$SRC/harvest.py" ] && [ -f "$SRC/dashboard.html" ] || die "Run install.sh from inside the downloaded Work Dashboard folder."

ARCH="$(uname -m)"
PY=""
for c in ${PYTHON:-} /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.1?/bin/python3; do
  [ -x "$c" ] || continue
  # needs 3.9+ and the Mac's own architecture (an Intel-only Python on Apple silicon cannot load EventKit)
  if "$c" -c "import sys,platform; assert sys.version_info>=(3,9) and platform.machine()=='$ARCH'" 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  warn "No Python 3.9 or newer for $ARCH found."
  say "  Install Apple's Command Line Tools (includes Python 3 and git):  xcode-select --install"
  say "  or Homebrew Python:                                              brew install python"
  say "  Then run ./install.sh again."
  exit 1
fi
ok "Python: $PY ($("$PY" --version 2>&1))"

# ---------- 2. questions ----------
CFG_OUT="${CONFIG_ONLY:-$APP/config.json}"
CONFIGURE=1
if [ -z "$CONFIG_ONLY" ] && [ -f "$APP/config.json" ]; then
  step "Existing settings found"
  if ask_yn "Keep your current settings ($APP/config.json)?" y; then CONFIGURE=0; fi
elif [ -z "$CONFIG_ONLY" ] && [ -f "$SRC/config.json" ]; then
  step "A config.json is next to the installer"
  if ask_yn "Use it instead of answering the questions?" y; then
    mkdir -p "$APP"; cp "$SRC/config.json" "$APP/config.json"; CONFIGURE=0
  fi
fi

WD_NAME=""; WD_OBS=0; WD_VAULT=""; WD_FOLDERS=""; WD_JIRA=0; WD_JSITE=""; WD_JEMAIL=""; WD_JPROJ=""
WD_OUTLOOK=0; WD_WEATHER=0; WD_PLACE=""; WD_LAT=""; WD_LON=""; WD_TAILSCALE=0

if [ "$CONFIGURE" = 1 ]; then
  step "About you"
  WD_NAME="$(ask "First name for the greeting (empty = none)" "")"

  # --- Obsidian ---
  step "Obsidian (optional): tasks and notes from your vault"
  if ask_yn "Do you use Obsidian and want its tasks and notes on the dashboard?" n; then
    WD_OBS=1
    # Obsidian keeps its list of vaults in obsidian.json
    VAULTS="$("$PY" - <<'PY' 2>/dev/null || true
import json, os, pathlib
p = pathlib.Path.home() / "Library/Application Support/obsidian/obsidian.json"
try:
    v = json.loads(p.read_text())["vaults"].values()
except Exception:
    v = []
for x in sorted(v, key=lambda x: -x.get("ts", 0)):
    if os.path.isdir(x.get("path", "")):
        print(x["path"])
PY
)"
    DEF_VAULT=""
    if [ -n "$VAULTS" ]; then
      say "Vaults Obsidian knows on this Mac:"
      i=0; while IFS= read -r v; do i=$((i+1)); say "  $i) $v"; done <<< "$VAULTS"
      pick="$(ask "Number of the vault to use, or a full path" "1")"
      if [[ "$pick" =~ ^[0-9]+$ ]]; then DEF_VAULT="$(sed -n "${pick}p" <<< "$VAULTS")"; else DEF_VAULT="$pick"; fi
    fi
    tries=0
    while true; do
      WD_VAULT="$(ask "Vault folder" "$DEF_VAULT")"
      WD_VAULT="${WD_VAULT/#\~/$HOME}"
      [ -n "$WD_VAULT" ] && [ -d "$WD_VAULT" ] && break
      warn "Folder not found: ${WD_VAULT:-(empty)}"
      tries=$((tries+1))
      if [ "$DEFAULTS" = 1 ] || [ "$tries" -ge 3 ]; then
        WD_OBS=0; warn "Obsidian stays off. Run ./install.sh again to set it up."; break
      fi
    done
    if [ "$WD_OBS" = 1 ]; then
      ok "Vault: $WD_VAULT"
      TOP="$(cd "$WD_VAULT" && find . -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sed 's|^\./||' | sort)"
      if [ -n "$TOP" ]; then
        say "Top-level folders (the dashboard reads .md files in the ones you pick, with subfolders):"
        i=0; while IFS= read -r f; do i=$((i+1)); say "  $i) $f"; done <<< "$TOP"
        DEF_PICK="all"; grep -qx "Inbox" <<< "$TOP" && DEF_PICK="$(grep -nx Inbox <<< "$TOP" | cut -d: -f1)"
        pick="$(ask "Numbers separated by commas, or 'all'" "$DEF_PICK")"
        if [ "$pick" = all ]; then WD_FOLDERS="$TOP"
        else
          WD_FOLDERS=""
          for n in ${pick//,/ }; do f="$(sed -n "${n}p" <<< "$TOP")"; [ -n "$f" ] && WD_FOLDERS+="$f"$'\n'; done
        fi
      else
        WD_FOLDERS=""
        warn "The vault has no folders; notes in its top level are not read. Pick folders later in Settings > Data."
      fi
    fi
  fi

  # --- Jira ---
  step "Jira Cloud (optional): your issues, Board moves, worklogs"
  if ask_yn "Do you use Jira Cloud and want its issues on the dashboard?" n; then
    WD_JIRA=1
    WD_JSITE="$(ask "Jira site, e.g. https://your-team.atlassian.net" "")"
    WD_JSITE="${WD_JSITE%/}"; [[ "$WD_JSITE" == http* ]] || WD_JSITE="https://$WD_JSITE"
    WD_JEMAIL="$(ask "Your Atlassian account email" "")"
    WD_JPROJ="$(ask "Project key to show (the letters before the issue number, e.g. ABC in ABC-12)" "")"
    say "An API token lets the dashboard read Jira as you. Create one at:"
    say "  https://id.atlassian.com/manage-profile/security/api-tokens"
    say "It is stored in your macOS Keychain (service 'work-dashboard-jira'), never in a file."
    TOKEN="$(ask_secret "Paste the API token (empty = add it later)")"
    if [ -n "$TOKEN" ] && [ -z "$CONFIG_ONLY" ]; then
      security add-generic-password -U -s work-dashboard-jira -a "$WD_JEMAIL" -w "$TOKEN" \
        && ok "Token saved in the Keychain."
      code="$(printf 'user = "%s:%s"\n' "$WD_JEMAIL" "$TOKEN" | curl -s -o /dev/null -w '%{http_code}' -K - "$WD_JSITE/rest/api/3/myself" || true)"
      if [ "$code" = 200 ]; then ok "Jira answered: signed in."; else warn "Jira test returned HTTP $code. Check site, email and token; the dashboard shows the error under Data sources."; fi
    elif [ -z "$TOKEN" ]; then
      say "  Later:  security add-generic-password -U -s work-dashboard-jira -a \"$WD_JEMAIL\" -w"
    fi
    unset TOKEN
  fi

  # --- Outlook ---
  if [ -d "/Applications/Microsoft Outlook.app" ]; then
    step "Microsoft Outlook (optional): category colours and Plan blocks with categories"
    if ask_yn "Use Microsoft Outlook for meeting categories? (needs an Exchange or Microsoft 365 account in Outlook)" n; then WD_OUTLOOK=1; fi
  fi

  # --- Weather ---
  step "Weather in the greeting (optional, Open-Meteo, free, no account)"
  place="$(ask "Your city (empty = no weather)" "")"
  if [ -n "$place" ]; then
    geo="$("$PY" - "$place" <<'PY' 2>/dev/null || true
import json, sys, urllib.parse, urllib.request
u = "https://geocoding-api.open-meteo.com/v1/search?count=1&format=json&name=" + urllib.parse.quote(sys.argv[1])
r = json.load(urllib.request.urlopen(u, timeout=10)).get("results") or []
if r:
    x = r[0]
    print(f'{x["name"]}\t{round(x["latitude"], 2)}\t{round(x["longitude"], 2)}\t{x.get("country", "")}')
PY
)"
    if [ -n "$geo" ]; then
      IFS=$'\t' read -r WD_PLACE WD_LAT WD_LON country <<< "$geo"
      WD_WEATHER=1; ok "Weather for $WD_PLACE, $country ($WD_LAT, $WD_LON). Only these coordinates are sent."
    else
      warn "Could not find '$place'; weather stays off (Settings > Data can turn it on later)."
    fi
  fi

  # --- write the config ---
  step "Writing settings"
  mkdir -p "$(dirname "$CFG_OUT")"
  export WD_NAME WD_OBS WD_VAULT WD_FOLDERS WD_JIRA WD_JSITE WD_JEMAIL WD_JPROJ WD_OUTLOOK WD_WEATHER WD_PLACE WD_LAT WD_LON
  "$PY" - "$SRC/config.example.json" "$CFG_OUT" <<'PY'
import json, os, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
e = os.environ
on = lambda k: e.get(k) == "1"
home = os.path.expanduser("~")
cfg["user_name"] = e.get("WD_NAME", "")
o = cfg["obsidian"]
o["enabled"] = on("WD_OBS")
if on("WD_OBS"):
    vault = e["WD_VAULT"]
    folders = [f for f in e.get("WD_FOLDERS", "").splitlines() if f.strip()]
    o["vault_path"] = "~" + vault[len(home):] if vault.startswith(home) else vault
    o["vault_name"] = os.path.basename(vault.rstrip("/"))
    o["folders"] = folders
    inbox = "Inbox" if "Inbox" in folders else (folders[0] if folders else "")
    # every write goes inside a folder you picked
    o["note_folder"] = o["inbox_folder"] = inbox
    o["review_folder"] = f"{inbox}/WorkDashboard" if inbox else ""
    o["capture_text_file"] = f"{inbox}/WorkDashboard/Capture from Work Dashboard.md" if inbox else ""
    o["capture_task_file"] = f"{inbox}/WorkDashboard/List from Work Dashboard.md" if inbox else ""
    o["capture_file"] = f"{inbox}/Capture.md" if inbox else ""
    o["review_auto"] = bool(inbox)
j = cfg["jira"]
j["enabled"] = on("WD_JIRA")
if on("WD_JIRA"):
    j.update(site=e["WD_JSITE"], email=e["WD_JEMAIL"], project=e.get("WD_JPROJ") or j.get("project", ""))
cfg["planner"]["outlook"] = on("WD_OUTLOOK")
cfg["calendar"]["category_colors"] = on("WD_OUTLOOK")
w = cfg["weather"]
w["enabled"] = on("WD_WEATHER")
if on("WD_WEATHER"):
    w.update(place=e["WD_PLACE"], lat=float(e["WD_LAT"]), lon=float(e["WD_LON"]))
tmp = sys.argv[2] + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
os.replace(tmp, sys.argv[2])
print("Settings written:", sys.argv[2])
PY
fi

if [ -n "$CONFIG_ONLY" ]; then exit 0; fi

# ---------- 3. install ----------
step "Installing into $APP"
mkdir -p "$APP/data" "$APP/logs" "$HOME/Library/LaunchAgents"
# run from a local copy, not from the download folder (which may be in a synced cloud folder)
cp "$SRC"/*.py "$SRC/dashboard.html" "$APP/"
if ! "$APP/.venv/bin/python" -c "import platform; assert platform.machine()=='$ARCH'" 2>/dev/null; then
  rm -rf "$APP/.venv"
  "$PY" -m venv "$APP/.venv"
fi
# a recent pip matters: the pip bundled with Apple's Python 3.9 picks a broken pyobjc release
"$APP/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP/.venv/bin/python" -m pip install --quiet -r "$SRC/requirements.txt"
ok "Python packages installed"

step "First data harvest"
say "macOS may now ask for access to Calendars and Reminders: click Allow."
"$APP/.venv/bin/python" "$APP/harvest.py" || warn "The first harvest reported a problem; the dashboard's Data sources panel shows details."

step "Starting the dashboard at login"
bash "$SRC/make_app.sh"

# ---------- 4. iPhone / iPad (optional) ----------
TS=""
for b in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
  command -v "$b" >/dev/null 2>&1 && { TS="$b"; break; }
done
step "iPhone and iPad access (optional, through Tailscale)"
if [ -n "$TS" ]; then
  if ask_yn "Share the dashboard with your own devices on your tailnet now (tailscale serve)?" n; then
    "$TS" serve --bg 8765 && ok "Shared. On the iPhone or iPad, open the link that 'auth.py show' prints (below)."
  fi
else
  say "Not set up: Tailscale is not installed. To open the dashboard on your iPhone or iPad later,"
  say "install Tailscale (tailscale.com/download) on the Mac and the device, then run:  tailscale serve --bg 8765"
fi

step "Done"
ok "Work Dashboard is running. Bookmark http://127.0.0.1:8765"
say "Other devices (iPad, iPhone): \"$APP/.venv/bin/python\" \"$APP/auth.py\" show"
say "Change settings: the gear in the dashboard, or edit $APP/config.json then run ./update.sh"
say "If Calendar or Reminders stay empty: System Settings > Privacy & Security > Calendars / Reminders > turn on WorkDashboard."
