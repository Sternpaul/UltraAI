// Log view: live tail of the Studio log (and server logs of processed songs) with level filter.
// Entries arrive over the shared SSE stream ("log" events); /api/log is only used for the
// initial backfill and as a 30 s fallback while the stream is down.

import { api, h } from "./api.js";
import { onLog, eventsConnected } from "./events.js";


const LEVELS = [["error", "Errors"], ["warning", "Warnings"], ["info", "Normal"], ["debug", "Debug"], ["trace", "Everything"]];
const LEVEL_N = { trace: 5, debug: 10, info: 20, warning: 30, error: 40 };  // same numbers as logs.py

export function openLog(root, crumbs) {
  crumbs.replaceChildren(h("a", { href: "#/" }, "Library"), " / Log");
  let level = "info", after = 0, filter = "", follow = true;
  let entries = [];
  const lines = h("div", { class: "lines", role: "log", "aria-live": "off" });
  const levelSel = h("select", { "aria-label": "Detail", onchange: e => { level = e.target.value; redraw(); } },
    ...LEVELS.map(([v, l]) => h("option", { value: v, selected: v === level }, l)));
  const search = h("input", { type: "search", placeholder: "Filter", "aria-label": "Filter log", oninput: e => { filter = e.target.value.toLowerCase(); redraw(); } });
  const followBox = h("input", { type: "checkbox", checked: true, onchange: e => { follow = e.target.checked; } });
  const note = h("span", { class: "muted" }, "Shows what is recorded at the detail level chosen in Settings.");
  root.append(h("section", { class: "logview" },
    h("div", { class: "bar-row" }, "Show", levelSel, search,
      h("label", {}, followBox, " follow"),
      h("button", { type: "button", onclick: copy }, "Copy"), h("span", { class: "spacer" }), note),
    lines));

  function row(e) {
    const t = new Date(e.t * 1000).toLocaleTimeString();
    return h("div", { class: `logline ${e.level}` }, `${t} `, h("span", { class: "lv" }, e.level.toUpperCase()),
      h("span", { class: "nm" }, `${e.name}: `), e.msg);
  }
  const visible = e => e.n >= (LEVEL_N[level] ?? 20)
    && (!filter || `${e.name} ${e.msg}`.toLowerCase().includes(filter));
  function redraw() { lines.replaceChildren(...entries.filter(visible).map(row)); if (follow) lines.scrollTop = lines.scrollHeight; }
  function push(got) {  // dedup by id: the SSE backlog and /api/log can overlap
    got = got.filter(e => e.id > after);
    if (!got.length) return;
    after = got[got.length - 1].id;
    entries.push(...got);
    if (entries.length > 5000) entries = entries.slice(-5000);
    lines.append(...got.filter(visible).map(row));
    while (lines.childElementCount > 5000) lines.firstChild.remove();
    if (follow) lines.scrollTop = lines.scrollHeight;
  }
  async function backfill() {  // initial fill + fallback poll while the SSE stream is down
    try { push(await api(`/api/log?after=${after}&level=trace`)); } catch { /* backend restarting */ }
  }
  async function copy() {
    const text = entries.filter(visible).map(e => `${new Date(e.t * 1000).toISOString()} ${e.level.toUpperCase()} ${e.name}: ${e.msg}`).join("\n");
    try { await navigator.clipboard.writeText(text); } catch { /* not allowed */ }
  }
  api("/api/settings").then(st => { if (st.log_level && st.log_level !== level) { levelSel.value = level = st.log_level; redraw(); } }).catch(() => {});
  const offLog = onLog(push);
  const fallback = setInterval(() => { if (!eventsConnected()) backfill(); }, 30000);
  backfill();
  return () => { offLog(); clearInterval(fallback); };
}
