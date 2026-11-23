// Small client for the Studio backend.  Mutating requests carry the X-UltraAI header.

export class ApiError extends Error {}

export async function api(path, { method = "GET", json, form } = {}) {
  const opts = { method, headers: {} };
  if (method !== "GET") opts.headers["X-UltraAI"] = "1";
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
    throw new ApiError(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

let toastTimer;
export function toast(msg, ms = 2800) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
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

/** "LRCLIB · Kingfishr – The Blade · synced · version 1 of 3" */
export function lyricsLabel(li, fallback = "") {
  if (!li || !li.source) return fallback ? `lyrics: ${fallback}` : "";
  if (li.source === "user") return "lyrics: yours";
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
