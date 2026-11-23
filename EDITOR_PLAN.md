# UltraAI Studio — plan (draft for review)

One web app that is the whole workflow: **add song → automatic conversion → review only where
the machine is unsure → export to USDX.**  The machine does ~85–90 %; the app's job is to make
the remaining 10 % a few minutes of clicking, dragging and listening.

## 1. What exists already

- Pipeline produces complete UltraStar files: lyrics (LRCLIB), word/syllable timing, **notes
  with pitch** (SwiftF0 → notes, ~74 % pitch-class agreement with hand-made files; octave
  is ignored by USDX scoring anyway), line breaks, golden/freestyle notes, BPM/GAP.
- Per-song sidecar JSON with per-line confidence and "review" flags (basic).
- `ultraai review`: a minimal localhost page (play, nudge whole lines, save).
- 290 hand-made library songs = ground truth to test how well "uncertain" flags catch errors.

## 2. Existing editors (what to copy, what to avoid)

| Editor | Good | Hard / missing |
|---|---|---|
| Yass Reloaded (Java desktop) | complete: piano roll, split/merge/move notes, library tools | dense UI, many modes, steep learning curve |
| Karedi (desktop) | clean piano roll, cross-platform | creating from scratch, no guidance where to look |
| UltraStar Play / Melody Mania editor | in-game, records your voice as pitch | game-first, clumsy for detailed edits |
| USDX built-in editor | in the target game | keyboard-only, very hard |
| Tap tools (ultrastart) | tapping lines in time is fast | lines only, no notes |

All of them assume you **start from nothing** and show the whole song equally.  Ours starts
from a near-finished song and **leads you to the problems**.

## 3. The app

### 3.1 Song list / pipeline board
- Every song with its status: `queued → separating → transcribing → aligning → needs review (n
  issues) → reviewed → exported`, plus `needs lyrics`, `failed`.
- Trust score per song; sort by "most issues first".
- **Add song**: paste a YouTube link (yt-dlp runs on the Studio machine; artist/title guessed from the
  video, editable), or pick/drop audio files or a folder.  Language auto-detected, editable.
- **Missing lyrics**: a paste box on the song; prefilled with Whisper's transcript as a draft.
  Saving re-runs only the cheap steps (seconds, everything else is cached).
- Batch run (the current library run) becomes low-priority queue items; new songs jump ahead.

### 3.2 Editor (one song)
Layout, top to bottom:
1. **USDX-style preview**: the two current lines, note bars, karaoke highlight, exactly like the
   game.  Always visible, so you check the result as you'll see it while singing.
2. **Timeline / piano roll**: vocal waveform + pitch curve of the singer in the background,
   note bars on top (syllable text inside), line breaks as vertical markers, playhead.
   Zoom/scroll with wheel/pinch; follows playback.
3. **Lyrics strip**: the line text, editable inline; syllable boundaries shown as `|`.
4. **Issues panel** (side): every uncertain spot, worst first; click = jump there, loop it.
   "OK" dismisses; the song is done when the list is empty.

Editing (mouse first, keyboard for speed):
- drag note = move in time; drag edges = resize; drag vertically = pitch (snaps to semitone,
  or "snap to singer" = pitch from the curve)
- drag a whole line / select several notes and move them together
- double-click text: fix lyrics; type `|` / backspace to split or merge syllables
  (re-splitting a note keeps the timing of its neighbours)
- line-break markers draggable; Enter in text = new line
- golden / freestyle / rap toggles on selection
- **tap mode**: loop a line, tap a key per syllable while listening → syllables retimed
  (fastest way to fix a badly timed line)
- playback: mix / vocals only / instrumental, 50–75 % speed without pitch change, loop
  selection, metronome-free; click anywhere on the timeline to play from there
- undo/redo, autosave, version history (original machine output always restorable)
- BPM/GAP never shown: the app keeps seconds internally and quantises on export.

### 3.3 Where the machine is unsure (issue detection)
Each line gets signals; their weighted combination ranks the issues:
- lyrics vs Whisper: line not heard, heard but different words, extra sung repeat not in the
  lyrics, ASR words left over (ad-libs / missing lines)
- timing sources disagree: LRC time vs Whisper time vs acoustic alignment (> 0.5 s)
- low alignment confidence (CTC posterior), anchoring failed, line squeezed / stretched
- notes: low pitch confidence, octave jumps, very short/long notes, notes over silence
- song level: no lyrics, LRC for another release (shifted / cut), Whisper heard little
**Calibrated on the 290 library songs**: we know where our output is wrong vs the hand-made
files, so we measure "share of real errors that are flagged" vs "number of flags" and tune
until e.g. 80 % of wrong lines are flagged while flagging < 20 % of lines.

### 3.4 Learning from you
Every edit is logged (before/after).  Corrections become training/eval data: e.g. a learned
timing-refinement model, and better issue scoring.

## 4. Technical shape: Studio app + remote worker

YouTube blocks many datacenter IPs ("sign in to confirm you're not a bot", checked 2026-11-22), so
downloading must happen on a machine with a regular connection.  Split:

```
 Studio machine                          server (processing server (Linux, CPU-capped slice))
 ┌───────────────────────────────┐   SSH        ┌──────────────────────────────┐
 │ UltraAI Studio                │ ───────────▶ │ ultraai worker               │
 │  - web UI on localhost        │  push audio  │  separation, Whisper,        │
 │  - song library (folders)     │  + job       │  alignment, pitch, notes     │
 │  - yt-dlp download, uploads   │ ◀─────────── │  (models + caches stay here) │
 │  - editor, playback, export   │  pull result │                              │
 └───────────────────────────────┘              └──────────────────────────────┘
```

- **Studio** (Studio machine): small Python app (FastAPI) serving the browser UI on localhost; owns the
  song library as plain USDX folders, so the game can read them directly.
- **Processor** is pluggable: `remote-ssh` (default: uses your existing SSH key/config, copies
  audio up with rsync, runs `ultraai process` there, copies back txt + sidecar + a compressed
  vocals track and pitch curve for the editor) or `local` (same code on the Studio machine, slow).
  Later: WireGuard instead of SSH changes nothing but the host name.
- Editing needs no server: works offline once a song is processed.  "Re-run with new lyrics"
  sends a small job; the server still has the cached stems, so it takes seconds.
- Frontend: TypeScript single-page app, canvas for the timeline/preview, Web Audio for
  playback/speed.  Shipped pre-built inside the Studio package (no Node needed on the Studio machine).
- Studio machine is **Windows**: Studio ships as one installer/.exe (PyInstaller) that opens its own
  app window (pywebview, Edge WebView2 which Windows already has) — looks like a desktop
  app, is the web UI inside; a normal browser tab works too.  Bundled: ffmpeg, yt-dlp + its
  JS runtime (deno).  SSH via Windows' built-in OpenSSH and your existing key; file transfer
  with SFTP (no rsync on Windows).

## 5. Phases
1. **Issue detection + calibration** on the library (no UI yet; measurable).
2. **Backend**: Studio app (song store, API, queue) + `ultraai process` worker CLI + SSH processor.
3. **Editor MVP**: preview, piano roll with waveform/pitch, drag/resize/pitch, text + syllable
   edit, issues panel, save/export.
4. **Workflow**: song board, YouTube/upload import, lyrics paste + re-run.
5. **Speed tools**: tap mode, slow playback, multi-select, shortcuts, version history.
6. **Learn from edits**.

Each phase is usable on its own; the library run keeps going in the background meanwhile.

## Phase 1 result (2026-11-22): issue detection

`ultraai/issues.py`: 27 evidence features per generated word (alignment confidence, voicing,
loudness, free vs anchored alignment, LRC deviation, Whisper agreement in time and text,
structure flags, repetitive text, syllable rate, Whisper words missing from the lyrics) ->
gradient-boosted risk model (`ultraai/issue_model.pkl`, fit by `scripts/issue_calibrate.py`
on 95 songs / 30k words with hand-made references) -> issues per screen line with plain-text
reasons, plus "missing lyrics" regions.  Written to the song sidecar (`issues`, `words`).

Song-grouped cross-validation (a line "needs review" if >= 1/3 of its words are > 0.5 s off
or not in the reference; 24 % of lines):

| lines flagged | wrong lines caught | flagged lines really wrong |
|---|---|---|
| 10 % | 37 % | 89 % |
| 20 % | 61 % | 73 % |
| 30 % | 72 % | 58 % |
| 40 % | 80 % | 48 % |

(old ctc/voicing rule at 20 %: 39 % caught, 56 % precise).  Default threshold flags ~20 %;
the editor sorts by risk, so the top of the list is nearly always a real problem.
Retrain when the library run has finished (3x the data), and later on the user's own edits.

## Phase 2 result (2026-11-22): Studio backend + worker

- `ultraai process JOBDIR` (`ultraai/worker.py`): job.json + audio -> out/song.txt, sidecar with
  issues, vocals.ogg (opus), editor.json (pitch curve 20 ms, waveform peaks 10 ms, Whisper
  words).  Status in status.json (stages); one heavy job at a time per machine (file lock);
  no lyrics -> state `needs_lyrics` with Whisper's transcript as a draft.
  `ultraai job-status JOBDIR --follow` streams status lines (one SSH connection per job).
- `ultraai studio` (`ultraai/studio/`): FastAPI on 127.0.0.1:8766, opens its own window
  (pywebview / WebView2) or the browser.
  - `store.py`: library = USDX song folders + hidden `.studio/` (state, editor media, pasted
    lyrics, every saved version; 000 = machine output).  Existing USDX songs are listed too.
  - `processors.py`: `SshProcessor` (system ssh/scp, BatchMode; job runs detached on the
    server via systemd-run in ultraai.slice, else nohup; Studio re-attaches after restart;
    remote job folder deleted after download) and `LocalProcessor`.
  - `importer.py`: audio files (tags or "Artist - Title" names) and YouTube via yt-dlp.
  - `doc.py`: editor document in seconds <-> txt; exact round trip on all 329 library and
    benchmark files, edits quantised on the song's own beat grid.
  - API: songs list/import (files, URL)/meta/lyrics/reprocess/delete, editor doc get/put
    (versioned, validated), issue dismiss, mark reviewed, media with HTTP ranges, settings,
    connection test.  Mutations need header `X-UltraAI: 1`.
- Studio machine install needs no AI libraries: base package = Studio; `pip install .[worker]` on the
  server.  Verified in a clean venv (no numpy/torch imported).
- Tests: fake-server end to end (upload -> remote job -> review -> edit -> versions; missing
  lyrics -> paste -> reprocess), real run on the server (cached song: 13 s).
- Not yet: the editor UI (phase 3; a placeholder list page exists), Windows installer
  (needs a Windows build: PyInstaller cannot cross-compile; GitHub Actions or any Windows machine).

## Phase 3 result (2026-11-23): editor UI

Plain ES modules in `ultraai/studio/web/` (no build step, no Node on the Studio machine):
`main.js` (router, library board, imports, settings, lyrics dialog), `editor.js` (editor),
`timeline.js` (piano roll + overview), `preview.js` (game-style preview), `model.js` (line text
/ syllables / pitch snapping), `api.js`, `app.css` (light + dark via `light-dark()`).

- Library: add YouTube link / files (button or drop anywhere), filters (needs review, needs
  lyrics, in progress, done, errors), search, per-song actions (Review/Open, Add lyrics with
  "start from what was heard", reprocess, replace lyrics, remove to .trash), live status.
- Editor: game preview (note bars + karaoke lyrics + next line); piano roll with semitone grid,
  singer pitch curve (octave-aligned), notes (golden/freestyle/rap colours), line bands and
  numbers, draggable line-break markers, issue regions, Whisper words band, vocal waveform,
  time ruler; whole-song overview with issues and viewport (click/drag to jump).
  Editing: click/shift-click/rubber-band select, drag to move (time + pitch, never across
  neighbours), drag edges to resize, arrows to nudge/transpose, snap pitch to the singer,
  split/merge/delete/add notes, note kinds, new line / join lines, line text editor with
  `|` syllables (same count: texts only; else re-timed over the line and pitched from the
  voice), tap mode (hold a key per syllable while it plays), loop, 50/75/100 % speed with
  pitch preserved, mix/vocals switch (falls back to vocals if the mix codec is unsupported),
  undo/redo (300 steps), autosave (1.2 s) with versions on disk, issue list with reasons,
  jump/loop/OK, Tab = next issue, lines list, "Mark as done", shortcut help (?).
- Tested in headless Chromium (`scripts/studio_ui_smoke.py`): open issue, play, nudge,
  transpose, undo, rubber band, line text edit -> autosave, tap mode re-times a line with the
  tapped rhythm, undo restores it, imported (non-Studio) song opens, 1280x720 and 1440x900,
  dark and light; no page errors.
- Still open: Windows installer (packaging), duet editing beyond switching singer,
  real-device check of audio latency in tap mode.
