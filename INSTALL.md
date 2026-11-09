# Installing Work Dashboard

About 5 minutes. Everything runs on your own Mac; nothing is uploaded anywhere.

## 1. What you need

| | Needed? | What for | How to get it |
|---|---|---|---|
| **Mac with macOS 13 (Ventura) or later** | Required | Apple silicon or Intel | |
| **Python 3.9 or later** | Required | Runs the dashboard | Comes with Apple's Command Line Tools: `xcode-select --install` (also gives you `git`). Or `brew install python`. |
| **git** | Required | Download and update | Command Line Tools, as above |
| **Apple Calendar / Reminders** | Required | Your meetings and to-dos | Add your accounts (iCloud, Google, Exchange / Microsoft 365...) in System Settings > Internet Accounts. The dashboard reads whatever Calendar.app shows. |
| **A web browser** | Required | Shows the dashboard | Safari, Chrome, Firefox, Arc... |
| Obsidian | Optional | Tasks (`- [ ]`) and notes from your vault, new notes, quick capture, weekly review | [obsidian.md](https://obsidian.md) |
| Jira Cloud | Optional | Your issues, Board moves, new issues, focus-timer worklogs | An Atlassian account and an [API token](https://id.atlassian.com/manage-profile/security/api-tokens) |
| Microsoft Outlook for Mac | Optional | Outlook category colours, time blocks booked with a category | Exchange or Microsoft 365 account in Outlook |
| Tailscale | Optional | Open the dashboard on your iPhone and iPad, privately | [tailscale.com/download](https://tailscale.com/download) on the Mac and the device |

Check Python and git in Terminal:

```bash
python3 --version
```

```bash
git --version
```

If either one asks to install the Command Line Tools, click **Install** and wait for it to finish.

## 2. Download and run the installer

```bash
git clone https://github.com/rtomkowski/work-dashboard.git ~/work-dashboard
```

```bash
cd ~/work-dashboard && ./install.sh
```

The installer asks a few questions. Press Return to accept the answer in [brackets].

1. **Your first name**, for the greeting (optional).
2. **Obsidian?** If yes, it lists the vaults Obsidian knows on this Mac; pick one, then pick the folders to read (subfolders included). New notes and captures are only ever written inside folders you picked. If no, everything Obsidian-related is hidden.
3. **Jira Cloud?** If yes: your site (`https://your-team.atlassian.net`), your Atlassian email, the project key (the `ABC` in `ABC-12`) and an API token. The token goes into your macOS Keychain, never into a file, and the installer checks that Jira accepts it.
4. **Microsoft Outlook?** Only asked when Outlook is installed.
5. **Your city**, for the weather in the greeting (empty = no weather). Only the city's coordinates are sent to Open-Meteo.
6. After installing: **iPhone/iPad access** through Tailscale, if Tailscale is installed.

Then it:

- copies the program to `~/Library/Application Support/WorkDashboard/` and creates a private Python environment there (your system Python is not changed);
- runs a first data harvest: **macOS asks for access to Calendars and Reminders: click Allow**;
- creates a small background app, `~/Applications/WorkDashboard.app`, that starts at login;
- opens **http://127.0.0.1:8765** in your browser. Bookmark it.

Want to look around first without giving any access? `python3 demo/serve_demo.py` and open http://127.0.0.1:8766 (made-up data, writes nothing).

### Installer options

| | |
|---|---|
| `./install.sh --defaults` | No questions: Calendar and Reminders only |
| `./install.sh --config-only my.json` | Only ask the questions and write the settings to `my.json` |
| `PYTHON=/path/to/python3 ./install.sh` | Use a specific Python |

Running `./install.sh` again is safe: it offers to keep your settings.

## 3. iPhone and iPad (optional)

The dashboard only listens on your Mac (`127.0.0.1`). To reach it from your own phone or tablet without opening it to the internet, use Tailscale:

1. Install Tailscale on the Mac and on the device, signed in to the same account.
2. On the Mac: `tailscale serve --bg 8765` (the installer offers to do this). With the App Store version of Tailscale the command is `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8765`.
3. Print your personal link (it carries an access token):

   ```bash
   ~/Library/Application\ Support/WorkDashboard/.venv/bin/python ~/Library/Application\ Support/WorkDashboard/auth.py show
   ```

4. Open that link once on the device. In Safari, **Share > Add to Home Screen** makes it open full screen like an app.

Every request needs the token, also on the Mac; the page keeps it in a cookie for about a year. `auth.py rotate` makes a new one (every device then enrols again).

## 4. Changing settings later

- Most settings: the **gear** in the dashboard (layout, profiles, work hours, Obsidian folders, Jira query, weather).
- Everything else: edit `~/Library/Application Support/WorkDashboard/config.json`, then run `./update.sh`. The README lists every setting.
- Turn Obsidian or Jira on or off: set `"enabled": true/false` under `obsidian` or `jira` in `config.json`, then `./update.sh`. To set up Jira after installing, also add the token:

  ```bash
  security add-generic-password -U -s work-dashboard-jira -a "you@example.com" -w
  ```

- **Statistics** group your meetings by rules on the event title (`stats.groups` in `config.json`). The defaults are examples for academic work; change them to your own categories.

## 5. Updating

```bash
cd ~/work-dashboard && git pull && ./update.sh
```

`update.sh` keeps your settings and only adds new ones.

## 6. Uninstalling

```bash
cd ~/work-dashboard && ./uninstall.sh
```

That stops the dashboard and removes it from login. `./uninstall.sh --purge` also deletes its settings, data, app and Keychain entries. Your calendars, reminders, notes and Jira issues are never touched.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `No Python 3.9 or newer found` | `xcode-select --install`, then run `./install.sh` again. On Apple silicon an old Intel-only Python is skipped on purpose. |
| Calendar or Reminders empty | System Settings > Privacy & Security > Calendars (and Reminders) > turn on **WorkDashboard**. Then press Refresh in the dashboard. |
| No permission prompt appeared | Run `bash make_app.sh` from the download folder and wait ~30 s. |
| Page does not load | `launchctl kickstart -k gui/$(id -u)/local.workdashboard`. Logs: `~/Library/Application Support/WorkDashboard/logs/err.log`. |
| "Unauthorized" or blank page | Open the token link from `auth.py show` (section 3) once in that browser. |
| Jira shows an error | Click the coloured dots (Data sources) for the reason. Usually a wrong site/email, an expired token, or a project key that does not exist. |
| Obsidian: "Folders not found" | Pick folders again under Settings > Data > Obsidian sources. A vault in iCloud or OneDrive must be downloaded to the Mac ("Always keep on this device"). |
| Something else | [Open an issue](../../issues) with macOS version, Python version and the last lines of `logs/err.log` (remove anything private). |
