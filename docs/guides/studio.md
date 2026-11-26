# UltraAI Studio guide

UltraAI Studio is the Windows desktop app: a song library, a processing queue, and a review
editor. Download the installer from the
[latest release](https://github.com/Sternpaul/UltraAI/releases/latest) (per-user install, no
admin rights).

## Architecture

```
Studio app (your PC)          processing server (Linux, CPU or GPU)
  web UI  <->  FastAPI  ──SSH/scp──>  ultraai worker (separation, Whisper, alignment, pitch)
  library = plain USDX folders <── results copied back
```

If you use a server, it only needs Python ≥ 3.11, ffmpeg and `pip install -e ".[worker]"` —
or the Docker image (`ghcr.io/sternpaul/ultraai-worker:<release tag>`, see README). The Studio
expects the repo in `~/UltraAI` there (configurable in Settings). Without a server, the same
worker runs locally on your PC.

## Setup

1. **Settings** → library folder. Processing mode: **"On this computer"** works out of the box
   (slower, ~10 min/song on CPU); **"On my server via SSH"** offloads to a Linux box
   (`user@server`, *Test connection* — must be non-interactive: passphrase-less key or
   ssh-agent). The server is optional, only for speed.
2. **Add songs**: YouTube link, *Add files…*, or drag & drop audio. The YouTube id is kept
   (`#AUDIOURL`/`#VIDEOURL`, `.ytid` sidecar), so YouTube captions can be used as a *last
   resort* lyrics source — such songs show a warning badge ("last resort — often inaccurate").
3. Processing runs on the server (~10 min per new song on CPU; much faster with a CUDA GPU).
   The song list updates live (server-sent events); click *steps* for per-stage progress and
   ETA. Multiple songs can process in parallel on the server (`ULTRAAI_MAX_JOBS`, default 1).

## The editor

- Top: game-style preview. Middle: piano roll with notes over the singer's pitch curve
  (orange), the heard words (grey) and the vocal waveform. Red areas mark review spots.
- **Issues tab**: machine-flagged problems (uncertain lines, pitch disagreements from the
  double-check models, lyrics/singing mismatches, validator warnings like overlapping notes).
  Every issue shows its **time in the song** and has a ⇥ button that jumps the timeline and
  playback there; `Tab`/`Shift+Tab` steps through them.
- Save warnings (e.g. "note at 2:03.5 overlaps the previous note (ends 2:03.8)") stay as a
  persistent toast with a **Show** button and appear in the issue list.
- Drag notes in time/pitch, drag edges, drag ▼ line-break markers, `N` adds a note.
  Double-click a note to edit its syllable; `Enter` edits the whole line (`|` = syllable
  separator). `T` tap mode re-times a line by holding keys. `P` sets selected notes to the
  sung pitch. ★ Golden notes re-places golden notes.
- **Versions**: every save is kept in `.studio/versions/`; the *Versions* dialog on a song
  card lists and restores them (restoring keeps a backup of the current file).
- Shortcuts: `Space` play, `L` loop, `V` vocals only, `[` `]` speed, `Ctrl+Z` undo, `?` help.

## Library layout

`Artist - Title/Artist - Title.txt` (USDX), the audio (`.m4a`/`.ogg`), optional `[VD].mp4` /
`[CO].jpg`, and `.studio/` with the app state, separated vocals, lyrics, and version history.
Point UltraStar Deluxe at the library root to sing. *USDB* in the editor shows the txt ready
for usdb.animux.de.

## Privacy

The app binds to `127.0.0.1` only and requires a per-install token (stored in the config dir,
mode 0600) for every non-GET request. If you set a YouTube `cookies.txt`, only its path is
ever used — the file is never read into logs, never uploaded, and never committed.
