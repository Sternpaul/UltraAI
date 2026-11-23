// Log view: live tail of the Studio log (and server logs of processed songs) with level filter.
import { api, h } from "./api.js";

const LEVELS = [["error", "Errors"], ["warning", "Warnings"], ["info", "Normal"], ["debug", "Debug"], ["trace", "Everything"]];

export function openLog(root, crumbs) {
  crumbs.replaceChildren(h("a", { href: "#/" }, "Library"), " / Log");
  let level = "info", after = 0, filter = "", follow = true;
  let entries = [];
  const lines = h("div", { class: "lines", role: "log", "aria-live": "off" });
  const levelSel = h("select", { "aria-label": "Detail", onchange: e => { level = e.target.value; after = 0; entries = []; lines.replaceChildren(); poll(); } },
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
  const visible = e => !filter || `${e.name} ${e.msg}`.toLowerCase().includes(filter);
  function redraw() { lines.replaceChildren(...entries.filter(visible).map(row)); if (follow) lines.scrollTop = lines.scrollHeight; }
  async function poll() {
    try {
      const got = await api(`/api/log?after=${after}&level=${level}`);
      if (got.length) {
        after = got[got.length - 1].id;
        entries.push(...got);
        if (entries.length > 5000) entries = entries.slice(-5000);
        lines.append(...got.filter(visible).map(row));
        while (lines.childElementCount > 5000) lines.firstChild.remove();
        if (follow) lines.scrollTop = lines.scrollHeight;
      }
    } catch { /* backend restarting */ }
  }
  async function copy() {
    const text = entries.filter(visible).map(e => `${new Date(e.t * 1000).toISOString()} ${e.level.toUpperCase()} ${e.name}: ${e.msg}`).join("\n");
    try { await navigator.clipboard.writeText(text); } catch { /* not allowed */ }
  }
  api("/api/settings").then(st => { if (st.log_level && st.log_level !== level) { levelSel.value = level = st.log_level; after = 0; entries = []; lines.replaceChildren(); poll(); } }).catch(() => {});
  poll();
  const timer = setInterval(poll, 2000);
  return () => clearInterval(timer);
}
