// One SSE stream from the Studio backend, shared by the library board and the log view:
// "songs" events (the full /api/songs payload) when the library changes, "log" events
// (new ring-buffer entries) as they happen.  We consume the stream with fetch +
// ReadableStream (not EventSource) so the per-install token stays in the x-ultraai
// header: query strings surface in access/log lines (level "trace"), so they must stay
// secret-free.  Reconnects keep the EventSource-style Last-Event-ID cursor: the newest
// seen log id is sent as ?after= on the next attempt, and the stream is retried after
// 30 s; while it is down the views fall back to their own 30 s poll (see main.js / log.js).

import { TOKEN } from "./api.js";

let running = false;
let lastLogId = 0;      // log cursor, sent as ?after= on (re)connect; the server also
                        // honours a Last-Event-ID header if one ever reaches it
let retryTimer = null;
const songCbs = new Set();
const logCbs = new Set();

export const eventsConnected = () => running; // false only while (re)connecting / retrying

export function onSongs(cb) { songCbs.add(cb); return () => songCbs.delete(cb); }

export function onLog(cb) { logCbs.add(cb); return () => logCbs.delete(cb); }

function handle(frame) {
  // frame = {event, id, data} parsed from the wire; `id` is the log cursor
  if (/^\d+$/.test(frame.id || "")) lastLogId = Math.max(lastLogId, +frame.id);
  let parsed;
  try { parsed = JSON.parse(frame.data); } catch { return; /* ignore a bad frame */ }
  if (frame.event === "log") {
    if (Array.isArray(parsed) && parsed.length) lastLogId = parsed[parsed.length - 1].id;
    logCbs.forEach(cb => cb(parsed));
  } else if (frame.event === "songs") {
    songCbs.forEach(cb => cb(parsed));
  }
}

export async function startEvents() {
  if (running) return;
  running = true;
  try {
    // Log cursor in the query string (a non-secret int), token in the header.
    const q = lastLogId ? `?after=${lastLogId}` : "";
    const resp = await fetch(`/api/events${q}`, { headers: { "x-ultraai": TOKEN } });
    if (!resp.ok || !resp.body) throw new Error(`events stream ${resp.status}`);
    // Parse the SSE wire format from the ReadableStream: a frame is the lines before a
    // blank line; "event:"/"data:"/"id:" fields (value after an optional single space),
    // lines starting with ":" are heartbeat comments.
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", frame = { event: "message", id: null, data: "" };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      for (;;) {
        const nl = buf.indexOf("\n");
        if (nl < 0) break;
        const line = buf.slice(0, nl).replace(/\r$/, "");
        buf = buf.slice(nl + 1);
        if (line === "") {                       // end of frame
          if (frame.data !== "") handle(frame);
          frame = { event: "message", id: null, data: "" };
          continue;
        }
        if (line.startsWith(":")) continue;      // heartbeat comment
        const c = line.indexOf(":");
        const k = line.slice(0, c);
        const v = line.slice(c + 1).replace(/^ /, "");
        if (k === "event") frame.event = v;
        else if (k === "id") frame.id = v;
        else if (k === "data") frame.data += (frame.data === "" ? "" : "\n") + v;
      }
    }
  } catch {
    // stream failed or was closed: fall through to the retry below
  } finally {
    running = false;
    clearTimeout(retryTimer);
    retryTimer = setTimeout(startEvents, 30000);  // fallback: retry (and poll) in 30 s
  }
}
