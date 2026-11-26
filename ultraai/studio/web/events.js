// One SSE stream from the Studio backend, shared by the library board and the log view:
// "songs" events (the full /api/songs payload) when the library changes, "log" events
// (new ring-buffer entries) as they happen.  EventSource cannot set headers, so the
// per-install token goes in the query string — GET /api/events accepts ?token= only.
// If the stream errors we retry after 30 s; while it is down the views fall back to
// their own 30 s poll (see main.js / log.js).

import { TOKEN } from "./api.js";

let es = null;
let lastLogId = 0;      // log cursor, sent as ?after= on (re)connect; the server also
                        // honours the Last-Event-ID header the browser resends
let connected = false;
let retryTimer = null;
const songCbs = new Set();
const logCbs = new Set();

export const eventsConnected = () => connected;

export function onSongs(cb) { songCbs.add(cb); return () => songCbs.delete(cb); }

export function onLog(cb) { logCbs.add(cb); return () => logCbs.delete(cb); }

export function startEvents() {
  if (es) return;
  const q = `?token=${encodeURIComponent(TOKEN)}${lastLogId ? `&after=${lastLogId}` : ""}`;
  es = new EventSource(`/api/events${q}`);
  es.addEventListener("songs", e => {
    try { songCbs.forEach(cb => cb(JSON.parse(e.data))); } catch { /* ignore a bad frame */ }
  });
  es.addEventListener("log", e => {
    try {
      const entries = JSON.parse(e.data);
      if (entries.length) lastLogId = entries[entries.length - 1].id;
      logCbs.forEach(cb => cb(entries));
    } catch { /* ignore a bad frame */ }
  });
  es.onopen = () => { connected = true; };
  es.onerror = () => {
    connected = false;
    if (es) { es.close(); es = null; }
    clearTimeout(retryTimer);
    retryTimer = setTimeout(startEvents, 30000);  // fallback: retry (and poll) in 30 s
  };
}
