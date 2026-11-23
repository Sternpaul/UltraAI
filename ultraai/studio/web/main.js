// Router, library board, settings, imports.
import { api, h, toast } from "./api.js";

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
    try {
      await api("/api/songs/url", { method: "POST", json: { url } });
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
    if (/^https?:/.test(s.source || "") && (!s.video || !s.cover) && s.state !== "downloading")
      acts.push(h("button", { type: "button", class: "ghost small", title: "Download the music video and cover (Settings decide the quality)",
        onclick: () => getExtras(s) }, "Video"));
    acts.push(h("button", { type: "button", class: "ghost small", title: "Remove from library", "aria-label": `Remove ${s.title}`, onclick: () => remove(s) }, "✕"));
    const sub = s.stage ? s.stage : s.lyrics_source ? `lyrics: ${s.lyrics_source}` : "";
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
    const stepsBtn = busy || p.stages?.length || s.state === "error"
      ? h("button", { type: "button", class: "linkish", onclick: () => stepsDialog(s.id) }, busy ? "steps" : "details") : null;
    return h("div", { class: "song", role: "listitem" },
      h("div", { class: "name" },
        h("div", { class: "title" }, s.title || "(untitled)"),
        h("div", { class: "artist" }, s.artist || "unknown artist"),
        s.error ? h("div", { class: "err" }, s.error) : null),
      h("div", { class: "status" }, h("span", { class: `chip ${s.state}` }, STATE_LABEL[s.state] || s.state), " ", stepsBtn,
        sub ? h("div", { class: "sub" }, sub, eta ? ` · ${eta}` : "") : null, bar),
      h("div", { class: "issues" }, s.state === "needs_review" ? `${s.open_issues} issue${s.open_issues === 1 ? "" : "s"}` : ""),
      h("div", { class: "acts" }, acts));
  }

  async function reprocess(s) {
    try { await api(`/api/songs/${s.id}/process`, { method: "POST" }); toast("Queued"); load(); }
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
const STEP_ICON = { done: "✓", running: "●", pending: "○", skipped: "–", error: "✕" };
async function stepsDialog(id) {
  stepsFor = id;
  const dlg = document.getElementById("steps-dialog");
  const s = (await api("/api/songs")).find(x => x.id === id);
  if (!s) return;
  fillSteps(s);
  const det = document.getElementById("steps-log");
  det.open = false;
  det.ontoggle = async () => {
    if (!det.open) return;
    const pre = det.querySelector("pre");
    pre.textContent = "Loading…";
    try { pre.textContent = (await api(`/api/songs/${id}/worker-log`)) || "No server log yet (it arrives with the results)."; }
    catch (e) { pre.textContent = e.message; }
  };
  dlg.addEventListener("close", () => { stepsFor = null; }, { once: true });
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
  eta.textContent = ["processing", "downloading"].includes(s.state) && Number.isFinite(p.eta)
    ? `About ${fmtDur(p.eta)} left${p.elapsed ? ` · running for ${fmtDur(p.elapsed)}` : ""}` : (s.error || "");
}
function refreshSteps(songs) {
  if (!stepsFor) return;
  const s = songs.find(x => x.id === stepsFor);
  if (s) fillSteps(s);
}

function sessionStorageGet(k) { try { return sessionStorage.getItem(`ultraai.${k}`); } catch { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(`ultraai.${k}`, v); } catch { /* ignore */ } }

// ------------------------------------------------------------------ uploads (drop anywhere)
async function upload(files) {
  files = files.filter(f => f.type.startsWith("audio/") || f.type.startsWith("video/") || /\.(mp3|m4a|ogg|opus|flac|wav|aac|webm)$/i.test(f.name));
  if (!files.length) return toast("No audio files in the selection");
  const form = new FormData();
  for (const f of files) form.append("files", f);
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
