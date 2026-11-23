// The song editor: playback, piano roll editing, line text, tap mode, issues, autosave.
import { api, fmtTime, h, lyricsLabel, toast } from "./api.js";
import {
  MIN_NOTE, applyLineText, copyToRepeats, curveOffset, curvePitch, flatten, isCont, lineEnd, lineStart, lineText,
  lineToEditText, normalize, repeatsOf, lineAt, copyLineNotes,
} from "./model.js";
import { drawPreview } from "./preview.js";
import { Timeline, drawOverview } from "./timeline.js";

const SHORTCUTS = [
  ["Space", "Play / pause"], ["Click", "Move the playhead (empty area) / select a note"],
  ["Drag note", "Move in time and pitch"], ["Drag note edge", "Change start / end"],
  ["Drag ▼ marker", "Move a line break"], ["Drag empty area", "Select several notes"],
  ["Shift+click", "Add to selection"], ["Double-click note", "Edit the line's text and syllables"],
  ["Double-click empty", "Add a note"], ["← / →", "Nudge selected notes 10 ms (Shift: 100 ms), else move playhead"],
  ["↑ / ↓", "Pitch ± 1 semitone (Shift: octave)"], ["P", "Pitch of selection = what is sung"],
  ["Enter", "Edit the current line's text"], ["T", "Tap mode: re-time the current line by tapping"],
  ["S", "Split note"], ["M", "Merge note with the next"], ["Delete", "Delete selected notes"],
  ["G / F / R / N", "Golden / freestyle / rap / normal note"], ["B", "Start a new line at the selected note"],
  ["C", "Copy this line's pitches and golden notes to all its repeats"],
  ["Shift+B", "Join the line with the next"], ["L", "Loop selection / current issue"],
  ["V", "Switch mix / vocals only"], ["[ / ]", "Slower / faster"], ["Tab / Shift+Tab", "Next / previous issue"],
  ["Ctrl+wheel, + / −", "Zoom"], ["Wheel", "Scroll"], ["Ctrl+Z / Ctrl+Y", "Undo / redo"], ["Ctrl+S", "Save now"],
  ["Esc", "Clear selection / leave tap mode"], ["?", "This help"],
];

export async function openEditor(root, id, crumbs) {
  let data;
  try {
    [data] = await Promise.all([api(`/api/songs/${id}/doc`)]);
  } catch (e) {
    root.append(h("div", { class: "empty" }, `Cannot open this song: ${e.message}`));
    return () => {};
  }
  const info = await api(`/api/songs/${id}`);
  crumbs.replaceChildren(h("a", { href: "#/" }, "Library"), " / ", `${info.artist} – ${info.title}`);
  const editorData = data.editor ? await api(data.editor).catch(() => null) : null;

  // ---------------------------------------------------------------- state
  const doc = data.doc;
  const ed = {
    voice: 0, lines: doc.voices[0].lines, selection: new Set(), editor: editorData,
    issues: data.issues.map(i => ({ ...i })).sort((a, b) => b.risk - a.risk),
    currentIssue: null, loop: null, band: null, hoverBreak: null, duration: 0, curveOffset: 0,
    pitchSpan: { lo: 0, hi: 12 },
  };
  const undo = [], redo = [];
  let dirty = false, saving = false, saveTimer = null, saveState = "saved";

  // ---------------------------------------------------------------- audio
  const mix = new Audio(data.audio ?? "");
  const voc = data.vocals ? new Audio(data.vocals) : null;
  for (const a of [mix, voc]) if (a) { a.preload = "auto"; a.preservesPitch = true; }
  let audio = mix, speed = 1;
  mix.addEventListener("loadedmetadata", () => { ed.duration = Math.max(ed.duration, mix.duration || 0); });
  const now = () => audio.currentTime || 0;
  function seek(t) { audio.currentTime = Math.max(0, Math.min(t, ed.duration || t)); render(); }
  function playing() { return !audio.paused; }
  function play() { return audio.play().catch(e => toast(`Cannot play: ${e.message}`)); }
  function togglePlay() { playing() ? audio.pause() : play(); }
  mix.addEventListener("error", () => {
    if (voc && audio === mix) { setSource(true); toast("This audio file cannot be played here – using the vocals track"); }
    else if (!voc) toast("This audio file cannot be played here");
  });
  function setSource(useVocals) {
    if (!voc) return toast("No separated vocals for this song");
    const target = useVocals ? voc : mix;
    if (target === audio) return;
    const t = now(), was = playing();
    audio.pause(); target.currentTime = t; target.playbackRate = speed; audio = target;
    if (was) play();
    srcBtn.setAttribute("aria-pressed", String(audio === voc));
  }
  function setSpeed(s) {
    speed = Math.min(1, Math.max(0.5, s));
    for (const a of [mix, voc]) if (a) a.playbackRate = speed;
    speedSel.value = String(speed);
  }

  // ---------------------------------------------------------------- layout
  const btn = (label, title, onclick, extra = {}) => h("button", { type: "button", title, "aria-label": title, onclick, ...extra }, label);
  const playBtn = btn("▶", "Play / pause (Space)", () => togglePlay(), { class: "primary" });
  const timeEl = h("span", { class: "time" }, "0:00.00");
  const speedSel = h("select", { "aria-label": "Speed", onchange: e => setSpeed(Number(e.target.value)) },
    ...[["1", "100 %"], ["0.75", "75 %"], ["0.5", "50 %"]].map(([v, l]) => h("option", { value: v }, l)));
  const srcBtn = btn("Vocals", "Hear only the separated vocals (V)", () => setSource(audio !== voc), { "aria-pressed": "false", disabled: !voc });
  const loopBtn = btn("Loop", "Loop the selection / current issue (L)", () => toggleLoop(), { "aria-pressed": "false" });
  const undoBtn = btn("↶", "Undo (Ctrl+Z)", () => doUndo(), { disabled: true });
  const redoBtn = btn("↷", "Redo (Ctrl+Y)", () => doRedo(), { disabled: true });
  const saveEl = h("span", { class: "savestate", role: "status" }, "Saved");
  const reviewedBtn = btn(info.state === "reviewed" ? "✓ Done" : "Mark as done", "Mark this song as reviewed", () => markReviewed(),
    { class: info.state === "reviewed" ? "" : "primary" });
  const voiceSel = doc.voices.length > 1 ? h("select", { "aria-label": "Singer", onchange: e => switchVoice(Number(e.target.value)) },
    ...doc.voices.map((v, k) => h("option", { value: String(k) }, `Singer ${v.voice}`))) : null;

  const toolbar = h("div", { class: "toolbar" },
    h("div", { class: "group" }, playBtn, timeEl, speedSel, srcBtn, loopBtn),
    h("div", { class: "group" },
      btn("Text", "Edit the line's text and syllables (Enter / double-click)", () => editLine()),
      btn("Tap", "Re-time the current line by tapping along (T)", () => startTap()),
      btn("Pitch ↦ voice", "Set the selected notes to the sung pitch (P)", () => snapPitch()),
      btn("Split", "Split the selected note (S)", () => splitNote()),
      btn("Merge", "Merge the selected note with the next (M)", () => mergeNote()),
      btn("Delete", "Delete the selected notes (Del)", () => deleteNotes())),
    h("div", { class: "group" },
      btn("★", "Golden note (G)", () => setKind("*")), btn("F", "Freestyle (F)", () => setKind("F")),
      btn("R", "Rap (R)", () => setKind("R")), btn("N", "Normal (N)", () => setKind(":")),
      btn("Copy to repeats", "Give every repeat of this line the same pitches and golden notes (C)", () => copyRepeats()),
      btn("New line", "Start a new line at the selected note (B)", () => breakLine()),
      btn("Join", "Join the line with the next (Shift+B)", () => joinLine())),
    h("div", { class: "group" }, undoBtn, redoBtn, voiceSel),
    h("span", { class: "spacer" }),
    h("div", { class: "group" }, saveEl, reviewedBtn, btn("?", "Shortcuts (?)", () => help())));

  const pvCanvas = h("canvas", { "aria-label": "Game preview" });
  const tlCanvas = h("canvas", { role: "application", "aria-label": "Notes timeline. Use the lines list for text." });
  const ovCanvas = h("canvas", { "aria-hidden": "true" });
  const preview = h("div", { class: "preview" }, pvCanvas);
  const timelineEl = h("div", { class: "timeline" }, tlCanvas);
  const overview = h("div", { class: "overview", title: "Whole song – click to jump" }, ovCanvas);
  const hint = h("div", { class: "hint" }, "Red areas: the machine is unsure. Orange curve: what is sung. Grey words: what speech recognition heard. Press ? for shortcuts.");

  const issueTab = h("button", { type: "button", role: "tab", "aria-selected": "true", onclick: () => showTab("issues") }, "Issues");
  const lineTab = h("button", { type: "button", role: "tab", "aria-selected": "false", onclick: () => showTab("lines") }, "Lines");
  const issueHead = h("div", { class: "panel-head" });
  const issueList = h("div", { class: "panel", role: "tabpanel" });
  const lineList = h("div", { class: "panel", role: "tabpanel", hidden: true });
  const li = data.lyrics_info || {};
  const lyrInfo = h("div", { class: "lyrinfo", title: li.structure_text || "" },
    lyricsLabel(li, data.lyrics_source) || "lyrics: unknown",
    li.structure_text ? h("div", { class: "muted" }, li.structure_text) : null);
  const side = h("aside", { class: "side" }, lyrInfo, h("div", { class: "tabs", role: "tablist" }, issueTab, lineTab), issueHead, issueList, lineList, hint);

  const shell = h("div", { class: "editor" }, toolbar, preview, timelineEl, overview, side);
  root.append(shell);
  const tl = new Timeline(tlCanvas, ed);

  // ---------------------------------------------------------------- derived data
  function refreshDerived() {
    ed.lines = doc.voices[ed.voice].lines;
    const ps = ed.lines.flatMap(l => l.notes.map(n => n.pitch));
    ed.pitchSpan = ps.length ? { lo: Math.min(...ps), hi: Math.max(...ps) } : { lo: 0, hi: 12 };
    ed.curveOffset = curveOffset(ed.editor, ed.lines);
    const last = ed.lines.reduce((m, l) => Math.max(m, lineEnd(l)), 0);
    ed.duration = Math.max(ed.duration, last + 2, mix.duration || 0);
  }
  refreshDerived();
  function switchVoice(k) { ed.voice = k; ed.selection.clear(); refreshDerived(); renderLines(); render(); }

  // ---------------------------------------------------------------- edits
  function snapshot() { return structuredClone(doc.voices); }
  function remember() {
    undo.push(snapshot()); if (undo.length > 300) undo.shift();
    redo.length = 0; updateUndo();
  }
  function restore(state) {
    const selIdx = selectedIdx();
    doc.voices = state; refreshDerived(); reselect(selIdx); changed(false);
  }
  function doUndo() { if (!undo.length) return; redo.push(snapshot()); restore(undo.pop()); updateUndo(); }
  function doRedo() { if (!redo.length) return; undo.push(snapshot()); restore(redo.pop()); updateUndo(); }
  function updateUndo() { undoBtn.disabled = !undo.length; redoBtn.disabled = !redo.length; }
  function selectedIdx() { return flatten(ed.lines).filter(x => ed.selection.has(x.n)).map(x => [x.li, x.ni]); }
  function reselect(idx) {
    ed.selection.clear();
    for (const [li, ni] of idx) { const n = ed.lines[li]?.notes[ni]; if (n) ed.selection.add(n); }
  }
  function changed(record = true) {
    normalize(ed.lines);
    refreshDerived();
    dirty = true;
    setSave("unsaved");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1200);
    renderLines(); render();
    void record;
  }
  function selected() { return flatten(ed.lines).filter(x => ed.selection.has(x.n)); }
  function needSel() { const s = selected(); if (!s.length) toast("Select a note first (click it)"); return s; }

  function currentLineIndex() {
    const s = selected();
    if (s.length) return s[0].li;
    const t = now();
    let best = 0;
    ed.lines.forEach((ln, i) => { if (ln.notes.length && lineStart(ln) <= t + 0.3) best = i; });
    return best;
  }

  function nudge(dt) {
    const s = selected(); if (!s.length) return seek(now() + dt * 10);
    remember();
    const d = clampShift(dt);
    for (const { n } of s) { n.start += d; n.end += d; }
    changed();
  }
  function clampShift(dt) {
    const flat = flatten(ed.lines);
    let lo = -Infinity, hi = Infinity;
    flat.forEach((x, k) => {
      if (!ed.selection.has(x.n)) return;
      const prev = flat.slice(0, k).reverse().find(y => !ed.selection.has(y.n));
      const next = flat.slice(k + 1).find(y => !ed.selection.has(y.n));
      lo = Math.max(lo, (prev ? prev.n.end : 0) - x.n.start);
      if (next) hi = Math.min(hi, next.n.start - x.n.end);
    });
    return Math.min(hi, Math.max(lo, dt));
  }
  function transpose(dp) {
    const s = needSel(); if (!s.length) return;
    remember(); for (const { n } of s) n.pitch += dp; changed();
  }
  function snapPitch() {
    const s = needSel(); if (!s.length) return;
    if (!ed.editor?.pitch) return toast("No pitch curve for this song");
    remember();
    for (const { n } of s) n.pitch = curvePitch(ed.editor, n.start, n.end, n.pitch);
    changed();
  }
  function setKind(kind) {
    const s = needSel(); if (!s.length) return;
    remember(); for (const { n } of s) n.kind = kind; changed();
  }
  function deleteNotes() {
    const s = needSel(); if (!s.length) return;
    remember();
    for (const { n, li } of s) ed.lines[li].notes = ed.lines[li].notes.filter(x => x !== n);
    ed.selection.clear(); changed();
  }
  function splitNote() {
    const s = needSel(); if (s.length !== 1) return s.length && toast("Select one note to split");
    const { n, li } = s[0];
    const t = now() > n.start + MIN_NOTE && now() < n.end - MIN_NOTE ? now() : (n.start + n.end) / 2;
    if (t - n.start < MIN_NOTE || n.end - t < MIN_NOTE) return toast("Note too short to split");
    remember();
    const trail = /\s$/.test(n.text) ? " " : "";
    const second = { start: t, end: n.end, pitch: n.pitch, text: "~" + trail, kind: n.kind };
    n.end = t; n.text = trail ? n.text.replace(/\s+$/, "") : n.text;
    const notes = ed.lines[li].notes; notes.splice(notes.indexOf(n) + 1, 0, second);
    ed.selection.clear(); ed.selection.add(second); changed();
  }
  function mergeNote() {
    const s = needSel(); if (s.length !== 1) return s.length && toast("Select one note");
    const { n, li } = s[0];
    const notes = ed.lines[li].notes, k = notes.indexOf(n), nx = notes[k + 1];
    if (!nx) return toast("No next note in this line");
    remember();
    n.end = nx.end;
    n.text = isCont(nx) ? n.text.replace(/\s+$/, "") + nx.text.trim().slice(1) + (/\s$/.test(nx.text) ? " " : "")
      : n.text.replace(/\s+$/, "") + nx.text;
    notes.splice(k + 1, 1); changed();
  }
  function breakLine() {
    const s = needSel(); if (!s.length) return;
    const { li, n } = s[0];
    const line = ed.lines[li], k = line.notes.indexOf(n);
    if (k === 0) return toast("This note already starts its line");
    remember();
    const rest = line.notes.splice(k);
    const prevEnd = lineEnd(line);
    ed.lines.splice(li + 1, 0, { notes: rest, break: line.break });
    line.break = prevEnd + Math.min(0.3, (rest[0].start - prevEnd) / 2);
    changed();
  }
  function copyRepeats() {
    const li = currentLineIndex(), line = ed.lines[li];
    if (!line) return;
    if (!repeatsOf(ed.lines, line).length) return toast("This line is not repeated in the song");
    remember();
    const [done, skipped] = copyToRepeats(ed.lines, line);
    if (!done) { undo.pop(); updateUndo(); }
    else changed();
    toast(`Copied to ${done} repeat${done === 1 ? "" : "s"}` +
      (skipped ? ` · ${skipped} skipped (different number of notes – fix their syllables first)` : ""));
  }
  function joinLine() {
    const li = currentLineIndex(), line = ed.lines[li], nx = ed.lines[li + 1];
    if (!nx) return toast("No next line");
    remember();
    line.notes.push(...nx.notes); line.break = nx.break; ed.lines.splice(li + 1, 1); changed();
  }
  function addNoteAt(t, pitch) {
    let li = 0;
    ed.lines.forEach((ln, i) => { if (ln.notes.length && lineStart(ln) <= t) li = i; });
    const line = ed.lines[li];
    if (!line) return;
    const after = line.notes.filter(n => n.start > t)[0];
    const before = [...line.notes].reverse().find(n => n.end <= t);
    const start = Math.max(t, before ? before.end : t);
    const end = Math.min(start + 0.25, after ? after.start : start + 0.25);
    if (end - start < MIN_NOTE) return toast("No room for a note here");
    remember();
    const n = { start, end, pitch, text: "~", kind: ":" };
    line.notes.push(n); ed.selection.clear(); ed.selection.add(n); changed();
    toast("Note added – use Text (Enter) to give it a syllable");
  }

  // ---------------------------------------------------------------- line text dialog
  function editLine(li = currentLineIndex()) {
    const line = ed.lines[li]; if (!line) return;
    const dlg = document.getElementById("line-dialog"), input = dlg.querySelector("input");
    document.getElementById("line-title").textContent = `Edit line ${li + 1}`;
    input.value = lineToEditText(line);
    dlg.returnValue = "";
    dlg.showModal(); input.focus(); input.select();
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "save" || input.value.trim() === lineToEditText(line)) return;
      remember();
      const ok = applyLineText(line, input.value, (a, b, near) => curvePitch(ed.editor, a, b, near));
      if (!ok) { undo.pop(); updateUndo(); return toast("A line needs at least one syllable"); }
      ed.selection.clear(); line.notes.forEach(n => ed.selection.add(n));
      changed();
    }, { once: true });
  }

  // ---------------------------------------------------------------- tap mode
  let tap = null;
  const banner = h("div", { class: "tapbanner", hidden: true });
  timelineEl.append(banner);
  function startTap() {
    const li = currentLineIndex(), line = ed.lines[li];
    if (!line?.notes.length) return;
    remember();
    tap = { li, k: 0, down: false, notes: line.notes };
    ed.selection.clear(); line.notes.forEach(n => ed.selection.add(n));
    banner.hidden = false;
    banner.textContent = `Tap mode – hold any letter key while each syllable is sung (${line.notes.length} notes). Esc cancels.`;
    seek(Math.max(0, lineStart(line) - 2.0));
    play();
  }
  function tapDown() {
    if (!tap || tap.down || tap.k >= tap.notes.length) return;
    const t = now(), n = tap.notes[tap.k];
    if (tap.k > 0) { const p = tap.notes[tap.k - 1]; p.end = Math.min(p.end, t); }
    const len = n.end - n.start;
    n.start = t; n.end = t + Math.max(MIN_NOTE, len);
    tap.down = true; render();
  }
  function tapUp() {
    if (!tap || !tap.down) return;
    const n = tap.notes[tap.k];
    n.end = Math.max(n.start + MIN_NOTE, now());
    tap.down = false; tap.k++;
    if (tap.k >= tap.notes.length) endTap(true);
    render();
  }
  function endTap(ok) {
    if (!tap) return;
    banner.hidden = true;
    if (ok) { changed(); toast("Line re-timed"); }
    else { const st = undo.pop(); if (st) restore(st); updateUndo(); }
    tap = null;
    setTimeout(() => audio.pause(), ok ? 700 : 0);
  }

  // ---------------------------------------------------------------- loop, issues, review
  function toggleLoop() {
    if (ed.loop) { ed.loop = null; loopBtn.setAttribute("aria-pressed", "false"); render(); return; }
    const s = selected();
    if (s.length) ed.loop = [Math.min(...s.map(x => x.n.start)) - 0.4, Math.max(...s.map(x => x.n.end)) + 0.4];
    else if (ed.currentIssue) ed.loop = [ed.currentIssue.start - 0.8, ed.currentIssue.end + 0.8];
    else { const ln = ed.lines[currentLineIndex()]; if (ln) ed.loop = [lineStart(ln) - 0.5, lineEnd(ln) + 0.5]; }
    loopBtn.setAttribute("aria-pressed", String(!!ed.loop));
    render();
  }
  function gotoIssue(is) {
    ed.currentIssue = is;
    ed.selection.clear();
    for (const ln of ed.lines) for (const n of ln.notes) if (n.end > is.start && n.start < is.end) ed.selection.add(n);
    const span = Math.max(4, (is.end - is.start) + 4);
    tl.pps = Math.min(400, Math.max(40, tl.w / span));
    tl.t0 = Math.max(-1, is.start - 1.5);
    seek(Math.max(0, is.start - 1));
    if (ed.loop) { ed.loop = [is.start - 0.8, is.end + 0.8]; }
    renderIssues(); render();
  }
  function stepIssue(dir) {
    const open = ed.issues.filter(i => !i.dismissed).sort((a, b) => a.start - b.start);
    if (!open.length) return toast("No open issues");
    const cur = ed.currentIssue;
    let k = cur ? open.findIndex(i => i === cur) : -1;
    if (k < 0) k = dir > 0 ? open.findIndex(i => i.start > now()) - 1 : open.length;
    gotoIssue(open[(k + dir + open.length) % open.length]);
  }
  function makeSame(is) {
    const dst = lineAt(ed.lines, is.start), src = lineAt(ed.lines, is.template_start);
    if (!dst || !src) return toast("Could not find the lines any more");
    remember();
    if (!copyLineNotes(src, dst)) { undo.pop(); updateUndo(); return toast("Different number of notes – fix the syllables first (Enter)"); }
    changed();
    dismiss(is);
    toast("Notes copied from the other repeat");
  }
  async function dismiss(is) {
    is.dismissed = true;
    try { await api(`/api/songs/${id}/issues/dismiss`, { method: "POST", json: { key: is.key } }); }
    catch (e) { toast(e.message); }
    renderIssues(); render();
  }
  async function markReviewed() {
    await save();
    try {
      await api(`/api/songs/${id}/reviewed`, { method: "POST" });
      reviewedBtn.textContent = "✓ Done"; reviewedBtn.className = "";
      toast("Marked as done");
    } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------- saving
  function setSave(state, msg = "") {
    saveState = state;
    saveEl.textContent = { unsaved: "Unsaved…", saving: "Saving…", saved: "Saved", error: "Not saved!" }[state];
    saveEl.title = msg;
    saveEl.classList.toggle("error", state === "error");
  }
  async function save() {
    clearTimeout(saveTimer);
    if (!dirty || saving) return;
    saving = true; dirty = false; setSave("saving");
    try {
      const r = await api(`/api/songs/${id}/doc`, { method: "PUT", json: { doc, note: "edit" } });
      setSave(dirty ? "unsaved" : "saved", (r.problems || []).join("\n"));
      if (r.problems?.length) toast(`Saved with warnings: ${r.problems[0]}`);
    } catch (e) {
      dirty = true; setSave("error", e.message); toast(`Save failed: ${e.message}`);
    } finally {
      saving = false;
      if (dirty && saveState !== "error") saveTimer = setTimeout(save, 1200);
    }
  }
  const beforeUnload = e => { if (dirty) { save(); e.preventDefault(); } };
  addEventListener("beforeunload", beforeUnload);

  // ---------------------------------------------------------------- side panels
  function showTab(which) {
    issueTab.setAttribute("aria-selected", String(which === "issues"));
    lineTab.setAttribute("aria-selected", String(which === "lines"));
    issueList.hidden = issueHead.hidden = which !== "issues";
    lineList.hidden = which !== "lines";
  }
  function riskColor(r) { return r > 0.8 ? "var(--bad)" : r > 0.6 ? "var(--warn)" : "var(--muted)"; }
  function renderIssues() {
    const open = ed.issues.filter(i => !i.dismissed).length;
    issueHead.replaceChildren(`${open} open of ${ed.issues.length}`, h("span", { class: "spacer" }),
      h("button", { type: "button", class: "small", onclick: () => stepIssue(1) }, "Next ⇥"));
    issueList.replaceChildren(...(ed.issues.length ? ed.issues.map(is =>
      h("div", { class: `item${is === ed.currentIssue ? " current" : ""}${is.dismissed ? " dismissed" : ""}`,
        onclick: () => gotoIssue(is) },
        h("span", { class: "risk", style: `background:${riskColor(is.risk)}`, title: `risk ${Math.round(is.risk * 100)} %` }),
        h("span", { class: "t" }, fmtTime(is.start).replace(/\.\d+$/, "")),
        h("div", { class: "txt" },
          h("div", {}, is.kind === "missing_lyrics" ? `Heard but not in lyrics: “${is.text}”` : is.text),
          h("div", { class: "why" }, (is.reasons || []).join(" · "))),
        is.dismissed || is.kind !== "repeat" ? null : h("button", { type: "button", class: "small primary",
          title: "Use the notes of the other repeats here", onclick: e => { e.stopPropagation(); makeSame(is); } }, "Make same"),
        is.dismissed ? null : h("button", { type: "button", class: "small ghost", title: "This is fine",
          onclick: e => { e.stopPropagation(); dismiss(is); } }, "✓ OK")))
      : [h("div", { class: "empty" }, "No issues found. Play it through once to be sure.")]));
  }
  let lineEls = [];
  function renderLines() {
    const flagged = ln => ed.issues.some(i => !i.dismissed && i.kind === "timing" && i.end > lineStart(ln) && i.start < lineEnd(ln));
    lineEls = ed.lines.map((ln, li) => h("div", {
      class: `item${flagged(ln) ? " flagged" : ""}`,
      onclick: () => { ed.selection.clear(); ln.notes.forEach(n => ed.selection.add(n)); tl.ensureVisible(lineStart(ln)); seek(lineStart(ln) - 0.3); },
      ondblclick: () => editLine(li),
    }, h("span", { class: "t" }, fmtTime(lineStart(ln)).replace(/\.\d+$/, "")), h("div", { class: "txt" }, lineText(ln))));
    lineList.replaceChildren(...lineEls);
  }
  let lastCurLine = -1;
  function markCurrentLine() {
    const t = now();
    let k = -1;
    ed.lines.forEach((ln, i) => { if (ln.notes.length && lineStart(ln) <= t) k = i; });
    if (k === lastCurLine) return;
    lineEls[lastCurLine]?.classList.remove("current");
    lineEls[k]?.classList.add("current");
    if (!lineList.hidden && playing()) lineEls[k]?.scrollIntoView({ block: "nearest" });
    lastCurLine = k;
  }

  // ---------------------------------------------------------------- pointer interaction
  let drag = null;
  const pos = e => { const r = tlCanvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  tlCanvas.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    tlCanvas.setPointerCapture(e.pointerId);
    const [x, y] = pos(e);
    const hit = tl.hit(x, y);
    if (hit.kind === "break") {
      drag = { kind: "break", li: hit.li, x0: x, orig: ed.lines[hit.li].break, moved: false, snap: snapshot() };
    } else if (hit.kind === "empty") {
      drag = { kind: "band", x0: x, y0: y, moved: false, add: e.shiftKey || e.ctrlKey };
    } else {
      if (e.shiftKey || e.ctrlKey) {
        ed.selection.has(hit.n) ? ed.selection.delete(hit.n) : ed.selection.add(hit.n);
      } else if (!ed.selection.has(hit.n)) { ed.selection.clear(); ed.selection.add(hit.n); }
      const orig = new Map([...ed.selection].map(n => [n, { start: n.start, end: n.end, pitch: n.pitch }]));
      drag = { kind: hit.kind === "note" ? "move" : hit.kind, n: hit.n, x0: x, y0: y, orig, moved: false, snap: snapshot() };
    }
    render();
  });
  tlCanvas.addEventListener("pointermove", e => {
    const [x, y] = pos(e);
    if (!drag) {
      const hit = tl.hit(x, y);
      tlCanvas.style.cursor = hit.kind === "start" || hit.kind === "end" || hit.kind === "break" ? "ew-resize"
        : hit.kind === "note" ? "grab" : "crosshair";
      const hb = hit.kind === "break" ? hit.li : null;
      if (hb !== ed.hoverBreak) { ed.hoverBreak = hb; render(); }
      return;
    }
    const dx = x - drag.x0, dy = y - (drag.y0 ?? y);
    if (!drag.moved && Math.hypot(dx, dy) < 3) return;
    drag.moved = true;
    const dt = dx / tl.pps;
    if (drag.kind === "band") { ed.band = [drag.x0, drag.y0, x, y]; }
    else if (drag.kind === "break") {
      const ln = ed.lines[drag.li], nx = ed.lines[drag.li + 1];
      ed.lines[drag.li].break = Math.min(nx ? lineStart(nx) : Infinity, Math.max(lineEnd(ln), drag.orig + dt));
    } else if (drag.kind === "move") {
      for (const [n, o] of drag.orig) { n.start = o.start; n.end = o.end; }
      const d = clampShift(dt);
      const dp = Math.round(-dy / Math.max(4, tl.rowH()));
      for (const [n, o] of drag.orig) { n.start = o.start + d; n.end = o.end + d; n.pitch = o.pitch + dp; }
    } else if (drag.kind === "start" || drag.kind === "end") {
      const n = drag.n, o = drag.orig.get(n);
      const flat = flatten(ed.lines), k = flat.findIndex(f => f.n === n);
      if (drag.kind === "start") {
        const lo = k > 0 ? flat[k - 1].n.end : 0;
        n.start = Math.min(o.end - MIN_NOTE, Math.max(lo, o.start + dt));
      } else {
        const hi = k + 1 < flat.length ? flat[k + 1].n.start : Infinity;
        n.end = Math.max(o.start + MIN_NOTE, Math.min(hi, o.end + dt));
      }
    }
    render();
  });
  tlCanvas.addEventListener("pointerup", e => {
    if (!drag) return;
    const [x] = pos(e);
    if (drag.kind === "band") {
      if (drag.moved && ed.band) {
        const [x0, y0, x1, y1] = ed.band;
        if (!drag.add) ed.selection.clear();
        for (const n of tl.notesIn(Math.min(x0, x1), Math.max(x0, x1), Math.min(y0, y1), Math.max(y0, y1))) ed.selection.add(n);
      } else {
        if (!drag.add) ed.selection.clear();
        seek(tl.t(x));
      }
      ed.band = null;
    } else if (drag.moved) {
      if (drag.snap) { undo.push(drag.snap); redo.length = 0; updateUndo(); } else remember();
      changed();
    } else if (drag.kind === "move" && !e.shiftKey && !e.ctrlKey) {
      seek(drag.n.start);
    }
    drag = null;
    render();
  });
  tlCanvas.addEventListener("dblclick", e => {
    const [x, y] = pos(e);
    const hit = tl.hit(x, y);
    if (hit.kind === "note" || hit.kind === "start" || hit.kind === "end") editLine(hit.li);
    else if (y > 24 && y < tl.pitchBottom) addNoteAt(tl.t(x), tl.pitchAtY(y));
  });
  tlCanvas.addEventListener("wheel", e => {
    e.preventDefault();
    const [x] = pos(e);
    if (e.ctrlKey || e.metaKey) tl.zoomAt(x, Math.exp(-e.deltaY * 0.0015));
    else tl.t0 = Math.max(-1, tl.t0 + (e.deltaY + e.deltaX) / tl.pps * 0.8);
    render();
  }, { passive: false });
  const ovJump = e => {
    const r = ovCanvas.getBoundingClientRect();
    const t = (e.clientX - r.left) / r.width * (ed.duration || 1);
    const [a, b] = tl.visible();
    tl.t0 = Math.max(-1, t - (b - a) / 2);
    if (!playing()) seek(t);
    render();
  };
  ovCanvas.addEventListener("pointerdown", e => { ovCanvas.setPointerCapture(e.pointerId); ovJump(e); });
  ovCanvas.addEventListener("pointermove", e => { if (e.buttons) ovJump(e); });
  pvCanvas.addEventListener("click", () => togglePlay());

  // ---------------------------------------------------------------- keyboard
  const onKey = e => {
    if (document.querySelector("dialog[open]")) return;
    if (e.target.closest?.("input, textarea, select")) return;
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    if (tap) {
      if (k === "Escape") { e.preventDefault(); endTap(false); }
      else if (/^[a-z]$/i.test(k) && !ctrl) { e.preventDefault(); tapDown(); }
      return;
    }
    if (ctrl && k.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? doRedo() : doUndo(); return; }
    if (ctrl && k.toLowerCase() === "y") { e.preventDefault(); doRedo(); return; }
    if (ctrl && k.toLowerCase() === "s") { e.preventDefault(); dirty = true; save(); return; }
    if (ctrl || e.altKey) return;
    const act = {
      " ": togglePlay, ArrowLeft: () => nudge(e.shiftKey ? -0.1 : -0.01), ArrowRight: () => nudge(e.shiftKey ? 0.1 : 0.01),
      ArrowUp: () => transpose(e.shiftKey ? 12 : 1), ArrowDown: () => transpose(e.shiftKey ? -12 : -1),
      Delete: deleteNotes, Backspace: deleteNotes, Enter: () => editLine(), Escape: () => { ed.selection.clear(); render(); },
      Tab: () => stepIssue(e.shiftKey ? -1 : 1), "?": help, "+": () => { tl.zoomAt(tl.x(now()), 1.25); render(); },
      "=": () => { tl.zoomAt(tl.x(now()), 1.25); render(); }, "-": () => { tl.zoomAt(tl.x(now()), 0.8); render(); },
      "[": () => setSpeed(speed - 0.25), "]": () => setSpeed(speed + 0.25), Home: () => seek(0),
    }[k] ?? {
      s: splitNote, m: mergeNote, g: () => setKind("*"), f: () => setKind("F"), r: () => setKind("R"), n: () => setKind(":"),
      p: snapPitch, t: startTap, l: toggleLoop, c: copyRepeats, v: () => setSource(audio !== voc), b: () => (e.shiftKey ? joinLine() : breakLine()),
    }[k.toLowerCase()];
    if (act) { e.preventDefault(); act(); }
  };
  const onKeyUp = e => { if (tap && /^[a-z]$/i.test(e.key)) { e.preventDefault(); tapUp(); } };
  addEventListener("keydown", onKey);
  addEventListener("keyup", onKeyUp);

  function help() {
    document.getElementById("help-grid").replaceChildren(...SHORTCUTS.flatMap(([k, d]) => [h("kbd", {}, k), h("span", {}, d)]));
    document.getElementById("help-dialog").showModal();
  }

  // ---------------------------------------------------------------- render loop
  let raf = 0, need = true;
  function render() { need = true; }
  function frame() {
    raf = requestAnimationFrame(frame);
    const t = now();
    if (ed.loop && playing() && t > ed.loop[1]) seek(ed.loop[0]);
    if (playing()) { tl.ensureVisible(t, 0.1); need = true; }
    if (!need) return;
    need = false;
    playBtn.textContent = playing() ? "❚❚" : "▶";
    timeEl.textContent = fmtTime(t);
    tl.draw(t);
    drawPreview(pvCanvas, ed.lines, t);
    drawOverview(ovCanvas, ed, tl, t);
    markCurrentLine();
  }
  for (const a of [mix, voc]) if (a) for (const ev of ["play", "pause", "seeked", "loadedmetadata"]) a.addEventListener(ev, render);
  const ro = new ResizeObserver(() => { tl.resize(); render(); });
  ro.observe(timelineEl); ro.observe(preview); ro.observe(overview);
  const themeMq = matchMedia("(prefers-color-scheme: dark)");
  const onTheme = () => { tl.resize(); render(); };
  themeMq.addEventListener("change", onTheme);

  // initial view: first issue, else start
  renderIssues(); renderLines(); updateUndo();
  tl.pps = 140;
  const first = ed.issues.find(i => !i.dismissed);
  tl.t0 = Math.max(-1, (ed.lines[0] ? lineStart(ed.lines[0]) : 0) - 1);
  if (first && info.state === "needs_review") setTimeout(() => gotoIssue(first), 50);
  frame();

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    themeMq.removeEventListener("change", onTheme);
    removeEventListener("keydown", onKey);
    removeEventListener("keyup", onKeyUp);
    removeEventListener("beforeunload", beforeUnload);
    for (const a of [mix, voc]) if (a) { a.pause(); a.src = ""; }
    if (dirty) save();
  };
}
