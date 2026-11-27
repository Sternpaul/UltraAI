// Router, library board, settings, imports.
import { api, h, lyricsLabel, toast } from "./api.js";

const view = document.getElementById("view");
const crumbs = document.getElementById("crumbs");
let cleanup = null;

const STATE_LABEL = {
  downloading: "Downloading", queued: "Queued", processing: "Processing", needs_lyrics: "Needs lyrics",
  needs_review: "Needs review", reviewed: "Done", imported: "Imported", error: "Error",
};
const FILTERS = [
  ["all", "All"], ["needs_review", "Needs review"], ["needs_lyrics", "Needs lyrics"],
  ["active", "In progress"], ["reviewed", "Done"], ["error", "Errors"],
];

async function route() {
  if (cleanup) { cleanup(); cleanup = null; }
  view.replaceChildren();
  const m = location.hash.match(/^#\/song\/([\w-]+)/);
  if (location.hash.startsWith("#/log")) {
    const { openLog } = await import("./log.js");
    cleanup = openLog(view, crumbs);
  } else if (m) {
    const { openEditor } = await import("./editor.js");
    cleanup = await openEditor(view, m[1], crumbs);
  } else {
    cleanup = library();
  }
  view.focus({ preventScroll: true });
}
addEventListener("hashchange", route);

// ------------------------------------------------------------------ library
function library() {
  crumbs.textContent = "Library";
  let filter = sessionStorageGet("filter") || "all";
  let query = "";
  const list = h("div", { class: "songs", role: "list" });
  const urlIn = h("input", { type: "url", placeholder: "Paste a YouTube link…", "aria-label": "YouTube link" });
  const fileIn = h("input", { type: "file", multiple: true, accept: "audio/*,video/*", hidden: true });
  const filterBtns = FILTERS.map(([k, label]) =>
    h("button", { type: "button", class: "small", "aria-pressed": String(k === filter),
      onclick: () => { filter = k; sessionStorageSet("filter", k); filterBtns.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.k === k))); render(); },
      dataset: { k } }, label));
  const search = h("input", { type: "search", placeholder: "Search", "aria-label": "Search songs",
    oninput: e => { query = e.target.value.toLowerCase(); render(); } });
  const counts = h("span", { class: "muted" });

  const addUrl = async e => {
    e.preventDefault();
    const url = urlIn.value.trim();
    if (!url) return;
    const options = await processDialog("Add song – processing options");
    if (!options) return;
    try {
      await api("/api/songs/url", { method: "POST", json: { url, options } });
      urlIn.value = "";
      toast("Downloading…");
      load();
    } catch (err) { toast(`Could not add: ${err.message}`); }
  };
  fileIn.addEventListener("change", () => { upload([...fileIn.files]); fileIn.value = ""; });

  view.append(h("section", { class: "library" },
    h("form", { class: "addbar", onsubmit: addUrl },
      urlIn, h("button", { class: "primary" }, "Add link"),
      h("button", { type: "button", onclick: () => fileIn.click() }, "Add files…"), fileIn,
      h("span", { class: "muted" }, "or drop audio files anywhere")),
    h("div", { class: "filters" }, filterBtns, counts, search),
    list));

  let songs = [];
  function render() {
    const shown = songs.filter(s => {
      if (filter === "active" && !["queued", "processing", "downloading"].includes(s.state)) return false;
      if (!["all", "active"].includes(filter) && s.state !== filter) return false;
      return !query || `${s.artist} ${s.title}`.toLowerCase().includes(query);
    });
    const review = songs.filter(s => s.state === "needs_review").length;
    const active = songs.filter(s => ["queued", "processing", "downloading"].includes(s.state)).length;
    counts.textContent = `${songs.length} songs · ${review} to review · ${active} in progress`;
    list.replaceChildren(...(shown.length ? shown.map(songRow)
      : [h("div", { class: "empty" }, songs.length ? "No songs match." : "No songs yet — add a link or drop audio files.")]));
  }

  function songRow(s) {
    const editable = ["needs_review", "reviewed", "imported"].includes(s.state);
    const acts = [];
    if (editable) acts.push(h("button", { type: "button", class: s.state === "needs_review" ? "primary" : "",
      onclick: () => { location.hash = `#/song/${s.id}`; } }, s.state === "needs_review" ? "Review" : "Open"));
    if (s.state === "needs_lyrics") acts.push(h("button", { type: "button", class: "primary", onclick: () => lyricsDialog(s.id, load) }, "Add lyrics"));
    if (["error", "needs_review", "reviewed", "needs_lyrics"].includes(s.state))
      acts.push(h("button", { type: "button", class: "ghost small", title: "Process again", onclick: () => reprocess(s) }, "↻"));
    if (["needs_review", "reviewed", "error"].includes(s.state))
      acts.push(h("button", { type: "button", class: "ghost small", title: "Replace the lyrics", onclick: () => lyricsDialog(s.id, load) }, "Lyrics"));
    acts.push(h("button", { type: "button", class: "ghost small", title: "Saved versions of the song text", "aria-label": `Versions of ${s.title}`,
      onclick: () => versionsDialog(s, load) }, "🕘"));
    if (/^https?:/.test(s.source || "") && (!s.video || !s.cover) && s.state !== "downloading")
      acts.push(h("button", { type: "button", class: "ghost small", title: "Download the music video and cover (Settings decide the quality)",
        onclick: () => getExtras(s) }, "Video"));
    acts.push(h("button", { type: "button", class: "ghost small", title: "Edit artist / title / language / video & cover links", "aria-label": `Edit ${s.title}`,
      onclick: () => infoDialog(s, load) }, "✎"));
    acts.push(h("button", { type: "button", class: "ghost small", title: "Remove from library", "aria-label": `Remove ${s.title}`, onclick: () => remove(s) }, "✕"));
    const sub = s.stage ? s.stage : lyricsLabel(s.lyrics_info, s.lyrics_source);
    const p = s.progress || {};
    const busy = ["processing", "downloading"].includes(s.state);
    let bar = null, eta = null;
    if (busy && p.stages?.length) {
      const done = p.stages.filter(x => ["done", "skipped"].includes(x.state)).length;
      const total = p.stages.length;
      const frac = Math.min(1, (done + (p.frac ?? 0)) / total);
      bar = h("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(Math.round(frac * 100)), "aria-label": "Progress" }, h("span", { style: `width:${(frac * 100).toFixed(1)}%` }));
      if (Number.isFinite(p.eta)) eta = `about ${fmtDur(p.eta)} left`;
    }
    const stepsBtn = busy || p.stages?.length || s.state === "error" || s.lyrics_info?.source
      ? h("button", { type: "button", class: "linkish", onclick: () => stepsDialog(s.id) }, busy ? "steps" : "details") : null;
    return h("div", { class: "song", role: "listitem" },
      h("div", { class: "name" },
        h("div", { class: "title" }, s.title || "(untitled)"),
        h("div", { class: "artist" }, s.artist || "unknown artist"),
        s.error ? h("div", { class: "err" }, s.error) : null,
        s.warning && s.state !== "error" ? h("div", { class: "warn", title: "Open the song: the first issue says why" }, `⚠ ${s.warning}`) : null),
      h("div", { class: "status" }, h("span", { class: `chip ${s.state}` }, STATE_LABEL[s.state] || s.state), " ", stepsBtn,
        sub ? h("div", { class: "sub" }, sub, eta ? ` · ${eta}` : "") : null, bar),
      h("div", { class: "issues" }, s.state === "needs_review" ? `${s.open_issues} issue${s.open_issues === 1 ? "" : "s"}` : ""),
      h("div", { class: "acts" }, acts));
  }

  async function reprocess(s) {
    const options = await processDialog(`Process again – ${s.title}`, s.options || null);
    if (!options) return;
    try { await api(`/api/songs/${s.id}/process`, { method: "POST", json: { options } }); toast("Queued"); load(); }
    catch (e) { toast(e.message); }
  }
  async function getExtras(s) {
    try { await api(`/api/songs/${s.id}/extras`, { method: "POST" }); toast("Downloading video / cover…"); load(); }
    catch (e) { toast(e.message); }
  }
  async function remove(s) {
    if (!confirm(`Remove "${s.artist} – ${s.title}" from the library? (moved to the .trash folder)`)) return;
    try { await api(`/api/songs/${s.id}`, { method: "DELETE" }); load(); } catch (e) { toast(e.message); }
  }

  async function load() {
    try { songs = await api("/api/songs"); render(); refreshSteps(songs); } catch (e) { toast(`Backend not reachable: ${e.message}`); }
  }
  load();
  const timer = setInterval(() => { if (!document.hidden) load(); }, 3000);
  libraryReload = load;
  return () => { clearInterval(timer); libraryReload = null; };
}
let libraryReload = null;

function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), r = sec % 60;
  return m < 60 ? `${m} min${r && m < 10 ? ` ${r} s` : ""}` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

// ------------------------------------------------------------------ processing steps dialog
let stepsFor = null;
let stepsLog = null;  // live worker-log poller of the open steps dialog: {id, pre, sum, bytes, lines, timer, live, busy}
const STEP_ICON = { done: "✓", running: "●", pending: "○", skipped: "–", error: "✕" };

// Cap of lines kept in the live <pre>: a long job must not grow the DOM without bound.
// 2000 lines ~ tens of KB of text, plenty for a session glimpse; anything older is dropped
// from the view only (the full log stays in <song>/.studio/worker.log).
const MAX_LOG_LINES = 2000;

function stopStepsLog() {
  if (stepsLog?.timer) clearInterval(stepsLog.timer);
  if (stepsLog) stepsLog.sum.textContent = "Server log of this song";
  stepsLog = null;
}

// Append text to the live <pre> without rebuilding its whole textContent every poll, and
// trim the oldest lines in batches of MAX_LOG_LINES so the trim itself stays cheap.
function appendWorkerLog(sl, text) {
  sl.pre.appendChild(document.createTextNode(text));
  sl.lines += (text.match(/\n/g) || []).length;
  if (sl.lines > MAX_LOG_LINES * 2) {
    const lines = sl.pre.textContent.split("\n");
    sl.pre.textContent = lines.slice(-MAX_LOG_LINES).join("\n");
    sl.lines = Math.min(sl.lines, MAX_LOG_LINES);
  }
  sl.pre.scrollTop = sl.pre.scrollHeight;
}

// One worker-log fetch; full=true reloads from byte 0, otherwise appends only the bytes past sl.bytes.
async function fetchWorkerLog(sl, full) {
  const r = await fetch(`/api/songs/${sl.id}/worker-log${full ? "" : `?after=${sl.bytes}`}`);
  const text = await r.text();
  if (!r.ok) throw new Error(text || `${r.status}`);
  const total = Number(r.headers.get("x-total-size"));
  if (full) {
    sl.pre.textContent = text || "No server log yet (it arrives with the results).";
    sl.lines = (sl.pre.textContent.match(/\n/g) || []).length;
    sl.pre.scrollTop = sl.pre.scrollHeight;
  } else if (text) appendWorkerLog(sl, text);
  sl.bytes = Number.isFinite(total) ? total : sl.bytes + new TextEncoder().encode(text).length;
}

// While the song is processing/downloading the server log streams live: poll ?after=<bytes>
// every 3 s and append; show "(live)".  When the job reaches a final state, grab the last
// bytes once and stop (the server then serves the authoritative final copy).
function setStepsLogLive(sl, live) {
  if (!stepsLog || stepsLog.id !== sl.id) return;
  // Unchanged flag: keep the existing poll timer — refreshSteps calls this every ~3 s and
  // clearing/re-creating the interval each time would reset the poll before it ever fires.
  if (live === sl.live) return;
  const wasLive = sl.live;
  sl.live = live;
  sl.sum.textContent = live ? "Server log of this song (live)" : "Server log of this song";
  if (sl.timer) { clearInterval(sl.timer); sl.timer = null; }
  if (live) {
    sl.timer = setInterval(async () => {
      if (!stepsLog || stepsLog.id !== sl.id || stepsLog.busy) return;
      stepsLog.busy = true;
      try { await fetchWorkerLog(stepsLog, false); } catch { /* retried on the next tick */ }
      finally { stepsLog.busy = false; }
    }, 3000);
  } else if (wasLive) {
    fetchWorkerLog(sl, false).catch(() => {});
  }
}

async function stepsDialog(id) {
  stepsFor = id;
  const dlg = document.getElementById("steps-dialog");
  const s = (await api("/api/songs")).find(x => x.id === id);
  if (!s) return;
  fillSteps(s);
  const det = document.getElementById("steps-log");
  det.open = false;
  stopStepsLog();
  det.ontoggle = async () => {
    if (!det.open) { stopStepsLog(); return; }
    const pre = det.querySelector("pre");
    const sl = stepsLog = { id, pre, sum: det.querySelector("summary"), bytes: 0, lines: 0, timer: null, live: false, busy: false };
    pre.textContent = "Loading…";
    try { await fetchWorkerLog(sl, true); }
    catch (e) { pre.textContent = e.message; return; }
    let live = false;
    try { live = ["processing", "downloading"].includes((await api(`/api/songs/${id}`)).state); } catch { /* not live */ }
    setStepsLogLive(sl, live);
  };
  dlg.addEventListener("close", () => { stepsFor = null; stopStepsLog(); }, { once: true });
  dlg.showModal();
}
function fillSteps(s) {
  document.getElementById("steps-title").textContent = `${s.artist} – ${s.title}`;
  const p = s.progress || {};
  const stages = p.stages || [];
  const list = document.getElementById("steps-list");
  list.replaceChildren(...(stages.length ? stages.map(x => {
    let dur = "";
    if (x.state === "done") dur = x.note === "cached" ? "already done" : x.seconds ? fmtDur(x.seconds) : "";
    else if (x.state === "running") dur = x.expected ? `${fmtDur(x.seconds || 0)} of ~${fmtDur(x.expected)}` : x.seconds ? fmtDur(x.seconds) : "";
    else if (x.state === "pending") dur = x.expected ? `~${fmtDur(x.expected)}` : "";
    else if (x.state === "skipped") dur = x.note || "";
    return h("li", { class: x.state }, h("span", { class: "ico", "aria-hidden": "true" }, STEP_ICON[x.state] || "?"),
      h("span", { class: "lbl" }, x.label), h("span", { class: "dur" }, dur));
  }) : [h("li", {}, h("span", {}), h("span", { class: "muted" }, s.error || s.stage || "No details yet"), h("span", {}))]));
  const eta = document.getElementById("steps-eta");
  const li = s.lyrics_info || {};
  const lyr = li.source ? `${lyricsLabel(li)}${li.structure_text ? ` – ${li.structure_text}` : ""}.` : "";
  eta.textContent = ["processing", "downloading"].includes(s.state) && Number.isFinite(p.eta)
    ? `About ${fmtDur(p.eta)} left${p.elapsed ? ` · running for ${fmtDur(p.elapsed)}` : ""}` : [s.error, lyr].filter(Boolean).join(" ");
}
function refreshSteps(songs) {
  if (!stepsFor) return;
  const s = songs.find(x => x.id === stepsFor);
  if (s) {
    fillSteps(s);
    if (stepsLog) setStepsLogLive(stepsLog, ["processing", "downloading"].includes(s.state));
  }
}

function sessionStorageGet(k) { try { return sessionStorage.getItem(`ultraai.${k}`); } catch { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(`ultraai.${k}`, v); } catch { /* ignore */ } }

// ------------------------------------------------------------------ uploads (drop anywhere)
async function upload(files) {
  files = files.filter(f => f.type.startsWith("audio/") || f.type.startsWith("video/") || /\.(mp3|m4a|ogg|opus|flac|wav|aac|webm)$/i.test(f.name));
  if (!files.length) return toast("No audio files in the selection");
  const options = await processDialog(`Add ${files.length} song${files.length > 1 ? "s" : ""} – processing options`);
  if (!options) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.append("options", JSON.stringify(options));
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`, 10000);
  try {
    await api("/api/songs/files", { method: "POST", form });
    toast(`Added ${files.length} song${files.length > 1 ? "s" : ""}`);
    if (location.hash.startsWith("#/song/")) location.hash = "#/";
    libraryReload?.();
  } catch (e) { toast(`Upload failed: ${e.message}`); }
}
const overlay = document.getElementById("drop-overlay");
let dragDepth = 0;
addEventListener("dragenter", e => { if (e.dataTransfer?.types.includes("Files")) { dragDepth++; overlay.hidden = false; } });
addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; overlay.hidden = true; } });
addEventListener("dragover", e => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
addEventListener("drop", e => {
  if (!e.dataTransfer?.files.length) return;
  e.preventDefault(); dragDepth = 0; overlay.hidden = true;
  upload([...e.dataTransfer.files]);
});

// ------------------------------------------------------------------ processing options
let modelsCache = null;
const SONG_S = 210;   // estimate for a 3½-minute song
const FIXED_RATE = 0.5 + 0.35 + 0.12;   // analysis, listening, aligning (s per s of audio)

/** Ask how to process: resolves to the options object, or null when cancelled. */
export async function processDialog(title = "Processing options", start = null) {
  const [models, settings] = await Promise.all([modelsCache ?? api("/api/models"), api("/api/settings")]);
  modelsCache = models;
  const o = { separation: "htdemucs", pitch: "swift", pitch_check: ["rmvpe"], sep_check: [], lead: "", notes: "singer",
    ...(settings.defaults || {}), ...(start || {}) };
  const dlg = document.getElementById("process-dialog"), box = document.getElementById("process-opts");
  document.getElementById("process-title").textContent = title;
  const best = (g, kind) => {  // best measured timing / pitch in a group, for the ★ marks
    const b = models[g].filter(m => m[kind]);
    return { timing: Math.max(...b.map(m => m[kind].timing ?? 0)), pitch: Math.max(...b.map(m => m[kind].pitch)) };
  };
  const scores = (g, m, kind) => {  // ★ only on the many-song scores when a group has them
    const star = kind === "wide" || !models[g].some(x => x.wide);
    const b = star ? best(g, kind) : {}, s = m[kind], parts = [];
    if (g !== "pitch") parts.push(`timing ${s.timing.toFixed(1)}${s.timing === b.timing ? " ★" : ""}`);
    parts.push(`pitch ${s.pitch.toFixed(1)}${s.pitch === b.pitch ? " ★" : ""}`);
    return parts.join(" · ");
  };
  const benchText = (g, m) => {
    if (g === "notes") return m.note;
    if (!m.bench && !m.wide) return m.key === "" ? "" : `not measured yet · ${costText(m.rate)}`;
    const ws = models.wide_songs?.[g];
    const parts = [];
    if (m.wide) parts.push(`${ws} songs: ${scores(g, m, "wide")}`);
    if (m.bench) parts.push(`${m.wide ? "3 songs: " : ""}${scores(g, m, "bench")}`);
    if (m.key) parts.push(costText(m.rate));
    return parts.join(" · ");
  };
  const radio = (group, key, legend) => h("fieldset", {}, h("legend", {}, legend),
    h("div", { class: "optlist" }, ...models[group].map(m => h("label", { class: "row" },
      h("input", { type: "radio", name: key, value: m.key, checked: o[key] === m.key, onchange: () => { o[key] = m.key; refresh(); } }),
      h("span", {}, m.name, m.by ? h("span", { class: "muted" }, ` · ${m.by}`) : null,
        h("div", { class: "muted" }, benchText(group, m)))))));
  const checks = (group, key, mainKey, legend) => h("fieldset", { dataset: { group } }, h("legend", {}, legend),
    h("div", { class: "optlist" }, ...models[group].map(m => h("label", { class: "row" },
      h("input", { type: "checkbox", name: key, value: m.key, checked: o[key].includes(m.key),
        onchange: e => { o[key] = e.target.checked ? [...o[key], m.key] : o[key].filter(x => x !== m.key); refresh(); } }),
      h("span", {}, m.name, m.by ? h("span", { class: "muted" }, ` · ${m.by}`) : null,
        h("div", { class: "muted" }, costText(m.rate)))))));
  const bsN = models.bench_songs_n || 0;
  box.replaceChildren(
    bsN ? h("p", { class: "muted benchnote" }, `Scores: % of words timed within 250 ms and % of notes on the right pitch, ` +
      `measured against hand-made songs. Models with an "N songs" score were tested on many songs, the others only on ` +
      `${bsN} songs - small differences there can be luck. ★ = best measured.`) : null,
    radio("separation", "separation", "Vocal separation"),
    radio("pitch", "pitch", "Pitch model"),
    checks("separation", "sep_check", "separation", "Double-check with these separations"),
    checks("pitch", "pitch_check", "pitch", "Double-check with these pitch models"),
    radio("lead", "lead", "Backing vocals"),
    radio("notes", "notes", "Notes"),
  );
  const eta = document.getElementById("process-eta");
  function refresh() {
    // the main model is not offered as its own double-check
    for (const [group, key, main] of [["separation", "sep_check", "separation"], ["pitch", "pitch_check", "pitch"]]) {
      for (const inp of box.querySelectorAll(`input[name=${key}]`)) {
        const isMain = inp.value === o[main];
        inp.disabled = isMain;
        if (isMain) { inp.checked = false; o[key] = o[key].filter(x => x !== inp.value); }
      }
      void group;
    }
    const rate = (k, g) => models[g].find(m => m.key === k)?.rate ?? 0;
    let r = FIXED_RATE + rate(o.separation, "separation") + rate(o.pitch, "pitch") + rate(o.lead, "lead");
    r += o.sep_check.reduce((s, k) => s + rate(k, "separation") + rate(o.pitch, "pitch"), 0);
    r += o.pitch_check.reduce((s, k) => s + rate(k, "pitch"), 0);
    eta.textContent = `About ${Math.max(1, Math.round(r * SONG_S / 60))} min for a 3½-minute song on a CPU without a GPU – ` +
      "much faster with a GPU. Steps already done for a song are reused.";
  }
  refresh();
  dlg.querySelector("[name=remember]").checked = false;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise(resolve => dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "start") return resolve(null);
    if (dlg.querySelector("[name=remember]").checked) {
      try { await api("/api/settings/defaults", { method: "POST", json: { options: o } }); } catch { /* keep going */ }
    }
    resolve(o);
  }, { once: true }));
}
function costText(rate) {
  const min = rate * SONG_S / 60;
  return min < 0.5 ? "seconds" : `+${Math.round(min) || 1} min`;
}

// ------------------------------------------------------------------ song info dialog
function ytidOf(u) {  // the 11-char video id of a YouTube link, else the trimmed link itself
  const m = (u || "").match(/(?:v=|youtu\.be\/|\/shorts\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : (u || "").trim();
}

function infoDialog(s, after) {
  const dlg = document.getElementById("info-dialog"), f = dlg.querySelector("form");
  f.elements.artist.value = s.artist || ""; f.elements.title.value = s.title || ""; f.elements.language.value = s.language || "";
  f.elements.video_url.value = s.video_link || ""; f.elements.cover_url.value = s.cover_link || "";
  const warn = document.getElementById("info-media-warn");
  // a downloaded [VD] file no longer matches when the video link points at another video
  const videoChanged = () => {
    const v = f.elements.video_url.value.trim();
    return !!(s.video && v && ytidOf(v) !== ytidOf(s.video_link || ""));
  };
  const syncWarn = () => { warn.hidden = !videoChanged(); };
  f.elements.video_url.addEventListener("input", syncWarn);
  syncWarn();
  dlg.returnValue = "";
  dlg.showModal();
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "save") return;
    const videoUrl = f.elements.video_url.value.trim(), coverUrl = f.elements.cover_url.value.trim();
    if (videoChanged() && !confirm("A [VD] video file is already downloaded from the old link — it will not match the new video link. Re-download via the Video button to replace it.")) return;
    try {
      await api(`/api/songs/${s.id}`, { method: "PATCH", json: {
        artist: f.elements.artist.value.trim(), title: f.elements.title.value.trim(), language: f.elements.language.value.trim(),
        // unchanged effective values stay derived (empty override) instead of being frozen
        video_url: videoUrl === (s.video_link || "") ? "" : videoUrl,
        cover_url: coverUrl === (s.cover_link || "") ? "" : coverUrl } });
      toast("Saved"); after?.();
    } catch (e) { toast(e.message); }
  }, { once: true });
}

// ------------------------------------------------------------------ lyrics dialog
export async function lyricsDialog(id, after) {
  const dlg = document.getElementById("lyrics-dialog");
  const ta = dlg.querySelector("textarea");
  const draftBtn = document.getElementById("lyrics-use-draft");
  const s = await api(`/api/songs/${id}`);
  document.getElementById("lyrics-title").textContent = `Lyrics – ${s.artist} – ${s.title}`;
  ta.value = s.lyrics || "";
  draftBtn.hidden = !s.lyrics_draft;
  draftBtn.onclick = () => { ta.value = s.lyrics_draft; ta.focus(); };
  dlg.returnValue = "";
  dlg.showModal();
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "save" || !ta.value.trim()) return;
    try { await api(`/api/songs/${id}/lyrics`, { method: "POST", json: { text: ta.value } }); toast("Lyrics saved – processing again"); after?.(); }
    catch (e) { toast(e.message); }
  }, { once: true });
}

// ------------------------------------------------------------------ versions dialog
async function versionsDialog(s, after) {
  const dlg = document.getElementById("versions-dialog");
  const list = document.getElementById("versions-list");
  document.getElementById("versions-title").textContent = `Versions – ${s.artist} – ${s.title}`;
  list.replaceChildren(h("li", { class: "muted" }, "Loading…"));
  dlg.showModal();
  let vs;
  try { vs = await api(`/api/songs/${s.id}/versions`); }
  catch (e) { list.replaceChildren(h("li", { class: "muted" }, e.message)); return; }
  list.replaceChildren(...(vs.length ? vs.map(v => h("li", {},
    h("span", {}, v.name, " ", h("span", { class: "meta" },
      `${new Date(v.mtime * 1000).toLocaleString()} · ${(v.size / 1024).toFixed(1)} KiB${v.what ? ` · ${v.what}` : ""}`)),
    h("button", { type: "button", class: "small", onclick: async () => {
      if (!confirm(`Restore ${v.name}? The current text is saved as a new version first.`)) return;
      try {
        await api(`/api/songs/${s.id}/versions/${encodeURIComponent(v.name)}/restore`, { method: "POST" });
        toast(`Restored ${v.name}`);
        dlg.close();
        after?.();
      } catch (e) { toast(e.message); }
    } }, "Restore"))
  ) : [h("li", { class: "muted" }, "No versions yet — every save in the editor creates one.")]));
}

// ------------------------------------------------------------------ settings
const sdlg = document.getElementById("settings-dialog");
const sform = document.getElementById("settings-form");
document.getElementById("open-settings").addEventListener("click", async () => {
  const st = await api("/api/settings");
  for (const k of ["library", "ssh_host", "ssh_port", "remote_dir"]) sform.elements[k].value = st[k] || "";
  sform.elements.youtube_audio.value = st.youtube_audio || "best";
  sform.elements.separation.value = st.separation || "htdemucs";
  sform.elements.log_level.value = st.log_level || "info";
  sform.elements.youtube_video.value = st.youtube_video || "none";
  sform.elements.youtube_cover.checked = !!st.youtube_cover;
  sform.elements.cookies.value = st.cookies || "";
  for (const r of sform.querySelectorAll("[name=processor]")) r.checked = r.value === st.processor;
  document.getElementById("settings-test-result").textContent = "";
  sdlg.returnValue = "";
  sdlg.showModal();
});
function settingsBody() {
  const f = sform.elements;
  return { library: f.library.value.trim(), processor: sform.querySelector("[name=processor]:checked")?.value || "ssh",
    ssh_host: f.ssh_host.value.trim(), ssh_port: Number(f.ssh_port.value) || 0, remote_dir: f.remote_dir.value.trim() || "UltraAI",
    youtube_audio: f.youtube_audio.value, youtube_video: f.youtube_video.value, youtube_cover: f.youtube_cover.checked,
    cookies: f.cookies.value.trim(),
    separation: f.separation.value, log_level: f.log_level.value };
}
document.getElementById("settings-test").addEventListener("click", async () => {
  const out = document.getElementById("settings-test-result");
  out.textContent = "Testing…";
  try {
    await api("/api/settings", { method: "PUT", json: settingsBody() });
    const r = await api("/api/settings/test", { method: "POST" });
    out.textContent = r.ok ? "Connection works." : `Failed: ${r.error}`;
  } catch (e) { out.textContent = `Failed: ${e.message}`; }
});
sdlg.addEventListener("close", async () => {
  if (sdlg.returnValue !== "save") return;
  try {
    const r = await api("/api/settings", { method: "PUT", json: settingsBody() });
    toast(r.restart_needed ? "Saved – restart the Studio to switch library folder" : "Settings saved");
  } catch (e) { toast(e.message); }
});

route();
