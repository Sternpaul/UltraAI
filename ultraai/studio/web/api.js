// Small client for the Studio backend.  Requests carry the per-install token (injected
// into index.html as a meta tag) in the x-ultraai header — including the SSE stream,
// which events.js consumes with fetch streaming, not EventSource.

export class ApiError extends Error {
  constructor(msg, status) {
    super(msg);
    this.status = status;
  }
}

export const TOKEN = document.querySelector('meta[name="ultraai-token"]')?.content || "";

export async function api(path, { method = "GET", json, form } = {}) {
  const opts = { method, headers: {} };
  if (method !== "GET") opts.headers["x-ultraai"] = TOKEN;
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  } else if (form) {
    opts.body = form;
  }
  const r = await fetch(path, opts);
  const isJson = (r.headers.get("content-type") || "").includes("json");
  const body = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = isJson ? (body.detail ?? JSON.stringify(body)) : body;
    throw new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg), r.status);
  }
  return body;
}

let toastTimer;

function hideToast() {
  clearTimeout(toastTimer);
  const el = document.getElementById("toast");
  el.classList.remove("show");
  el.classList.remove("has-action");
}

/** toast(msg, msNumber) as before, or toast(msg, { ms, action: { label, fn } }).
 *  ms 0 (or Infinity) keeps the toast until it is clicked; an action button is
 *  appended and clicking it hides the toast and runs fn. */
export function toast(msg, opts = 2800) {
  const el = document.getElementById("toast");
  const ms = typeof opts === "number" ? opts : (opts?.ms ?? 2800);
  const action = typeof opts === "object" && opts !== null ? opts.action : null;
  el.replaceChildren(String(msg));
  el.classList.toggle("has-action", !!action);
  if (action) {
    el.append(" ", h("button", { type: "button", class: "toast-action",
      onclick: () => { hideToast(); action.fn(); } }, action.label));
  }
  el.classList.add("show");
  clearTimeout(toastTimer);
  if (ms > 0 && Number.isFinite(ms)) toastTimer = setTimeout(hideToast, ms);
}

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** "LRCLIB · Kingfishr – The Blade · synced · version 1 of 3"
 *  YouTube-caption sources get an explicit warning: measured unreliable as lyric
 *  text (word_recall −60.8 vs hand-made references), so the user should review. */
export function lyricsLabel(li, fallback = "") {
  if (!li || !li.source) return fallback ? `lyrics: ${fallback}` : "";
  if (li.source === "user") return "lyrics: yours";
  if (li.source === "yt") return "lyrics: YouTube captions ⚠ last resort — often inaccurate, please review";
  const parts = [`lyrics: ${li.source}`];
  if (li.artist || li.title) parts.push(`${li.artist} – ${li.title}`);
  parts.push(li.synced ? "synced" : "not synced");
  if (li.versions > 1) parts.push(`version ${li.version} of ${li.versions}`);
  return parts.join(" · ");
}

export function fmtTime(t) {
  if (!Number.isFinite(t)) return "–";
  const m = Math.floor(t / 60), s = t - 60 * m;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}
