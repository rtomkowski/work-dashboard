// README screenshots from the demo server, through headless Google Chrome (DevTools protocol, no npm packages).
//   python3 demo/serve_demo.py &        then        node demo/screenshots.mjs
// Writes docs/screenshots/*.png, 1440 wide at 2x; each shot starts at 1440x900 and grows taller until no scroll area
// (main column, sidebar, right panel, an open dialog) hides anything, so the whole dashboard is in the picture (max 4000 px).
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const URL_ = process.env.DEMO_URL || "http://127.0.0.1:8766/";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "docs", "screenshots");
const PORT = 9333;

// name, script run on the page before the shot, theme
const SHOTS = [
  ["day", `setView("day")`, "light"],
  ["event-panel", `setView("day"); evOpen(DATA.calendar.events.find(e => e.title === "Project review with partners" && same(P(e.start), new Date())).id)`, "light"],
  ["week", `setView("week")`, "light"],
  ["week-grid", `S.weekLay = "cols"; setView("week")`, "light"],
  ["year", `setView("year")`, "light"],
  ["month", `setView("month")`, "light"],
  ["board", `setView("board")`, "light"],
  ["stats-history", `ST.mode = "history"; setView("stats")`, "light"],
  ["stats-ahead", `ST.mode = "ahead"; setView("stats")`, "light"],
  ["new-reminders", `setView("week"); openRem()`, "light"],
  ["plan-blocks", `setView("week"); openPlan()`, "light"],
  ["weekly-review", `setView("review")`, "light"],
  ["palette", `setView("week"); openPal(); $("palQ").value = "lab"; $("palQ").oninput()`, "light"],
  ["new-note", `setView("day"); openNote({title: "Idea: ML for defect detection"})`, "light"],
  ["meeting-prep", `setView("day"); const e = DATA.calendar.events.filter(x => x.title === "Project sync: Surface Lab" && P(x.start) > new Date()).sort((a, b) => P(a.start) - P(b.start))[0]; if (e) { S.anchor = sod(P(e.start)); evOpen(e.id); }`, "light"],
  ["inbox-triage", `setView("week"); openInbox()`, "light"],
  ["new-jira-issue", `setView("board"); openJira()`, "light"],
  ["data-sources", `setView("week"); openHealth()`, "light"],
  ["week-dark", `setView("week")`, "dark"],
];

const sleep = ms => new Promise(r => setTimeout(r, ms));
mkdirSync(OUT, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, "--hide-scrollbars",
  `--user-data-dir=${mkdtempSync(join(tmpdir(), "wd-shots-"))}`, "about:blank"], { stdio: "ignore" });

let target;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === "page"); } catch {}
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let seq = 0;
const pending = new Map();
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) => new Promise(r => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const evalVal = async expr => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result?.result?.value;
const OVERFLOW = `(() => { let x = document.documentElement.scrollHeight - innerHeight;
  for (const e of document.querySelectorAll("*")) { const s = getComputedStyle(e);
    if (/(auto|scroll)/.test(s.overflowY) && e.clientHeight > 60 && e.offsetParent !== null) x = Math.max(x, e.scrollHeight - e.clientHeight); }
  return Math.max(0, Math.ceil(x)); })()`;
const UNCAP = `(() => {
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
})()`;
const W = 1440, H0 = 900, HMAX = 4000;
const size = h => send("Emulation.setDeviceMetricsOverride", { width: W, height: h, deviceScaleFactor: 2, mobile: false });
async function fitHeight() {   // layouts reflow when the window grows, so measure a few times
  let h = H0;
  await evalVal(UNCAP);
  for (let i = 0; i < 4; i++) {
    const extra = await evalVal(OVERFLOW);
    if (!(extra > 2) || h >= HMAX) return;
    h = Math.min(HMAX, h + extra); await size(h); await sleep(600);
  }
}
const run = async expr => {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(`${expr}: ${r.result.exceptionDetails.exception?.description}`);
};

for (const [name, script, theme] of SHOTS) {
  await size(H0);
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await send("Page.navigate", { url: URL_ });
  await sleep(1500);
  await run(`L.theme = "${theme}"; applyTheme(); ${script}`);
  await sleep(1500);
  await fitHeight();
  const { result } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(OUT, `${name}.png`), Buffer.from(result.data, "base64"));
  console.log("wrote", name);
}
ws.close();
chrome.kill();
