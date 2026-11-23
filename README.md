# UltraAI

Turns songs into [UltraStar Deluxe](https://usdx.eu) karaoke files automatically: lyrics,
timing, line breaks and notes. Lyrics are fetched from LRCLIB (or pasted by you) and treated
as the truth; the audio decides *when* each syllable is sung and at which pitch. A risk model
marks the places where the machine is unsure, and **UltraAI Studio** takes you straight
there to fix them.

## UltraAI Studio (Windows app)

Download the installer from the [latest release](https://github.com/Sternpaul/UltraAI/releases/latest)
and run it (per-user install, no admin rights; Windows may warn about an unknown publisher:
*More info → Run anyway*).

1. **Settings** → choose the song library folder and enter the SSH host of the processing
   server (`user@server`, same as you log in with). **Test connection**.
   The connection is non-interactive: use a key without passphrase or load it into
   Windows' ssh-agent (`ssh-add`).
2. **Add songs**: paste a YouTube link, use *Add files…*, or drop audio files on the window.
   Processing runs on the server (about 10 minutes per new song); the song list shows
   each step with an ETA – click *steps* for details.
3. **Review**: songs marked *Needs review* open in the editor at the first uncertain spot.
4. Songs without lyrics online get *Add lyrics* (prefilled with what speech recognition heard).

The library is a normal USDX song folder (`Artist - Title/Artist - Title.txt`, `.m4a`/`.ogg`,
`[VD].mp4`, `[CO].jpg`; the YouTube link is kept as `#AUDIOURL`/`#VIDEOURL`) – point UltraStar Deluxe at it.

### Editor in one minute

* Top: preview as the game shows it. Middle: notes over the singer's pitch curve (orange),
  with what speech recognition heard (grey words) and the vocal waveform. Red areas are
  places to check; the list on the right explains why. `Tab` jumps to the next one.
* The issue list also checks live, while you edit: words of the lyrics missing in the song
  (red ▲), words that are not in the lyrics (red dashed notes), notes that do not match the
  voice, flat notes where the voice moves, and unusual jumps (often backing vocals or noise).
* Drag notes in time/pitch, drag their edges, drag the ▼ line-break markers. `N` adds a note.
* Double-click a note to change its syllable (`Tab` = next note); `Enter` edits the whole line
  (`|` separates syllables: `Re|lease my bo|dy`).
* *★ Golden notes…* re-places golden notes (hard jumps, big held notes, highest notes, mix).
* *USDB* shows the txt ready for usdb.animux.de (`#VIDEO:v=<YouTube id>` for USDB Syncer).
* `T` (tap mode): the line plays, hold any letter key for each syllable – fastest way to
  re-time a line. `P` sets selected notes to the sung pitch.
* `Space` play, `L` loop, `V` vocals only, `[` `]` speed, `Ctrl+Z` undo, `?` all shortcuts.
* Everything saves automatically; every version is kept in `.studio/versions/`.

### Settings worth knowing

| Setting | Default | |
|---|---|---|
| YouTube audio | Best available | Usually Opus, stored as `.ogg` without re-encoding |
| Music video | Up to 1080p | H.264 where possible; set as `#VIDEO` |
| Cover | on | Video thumbnail as `#COVER` |
| Vocal separation | Standard | ~5 min/song; "cleaner vocals" takes 20–30 min |
| Log detail | Normal | *Everything* logs every request, SSH command and download message |

Logs: the *Log* page in the app, or `%APPDATA%\UltraAI\studio.log`.

## Processing server

Linux with Python ≥ 3.11 and ffmpeg (CPU is enough; a GPU makes it ~10× faster):

```bash
git clone https://github.com/Sternpaul/UltraAI.git && cd UltraAI
python3 -m venv .venv && .venv/bin/pip install -e ".[worker]"
```

The Studio expects it in `~/UltraAI` (configurable). Jobs run one at a time in the
background (`systemd-run` in the `ultraai.slice` user slice when available), so closing the
Studio machine does not stop them. Optional CPU cap: `~/.config/systemd/user/ultraai.slice` with
`[Slice]` / `CPUQuota=320%`.

## Command line

```
ultraai process JOBDIR        process one job folder (what the Studio runs on the server)
ultraai studio [--no-window]  run the Studio (any OS; opens a browser without pywebview)
ultraai batch ...             convert a folder of audio files
ultraai eval ...              score against hand-made songs
ultraai check FILE.txt        validate an UltraStar file
```

## Development

* `ultraai/` – pipeline (`generate.py`, `align.py`, `structure.py`, `issues.py`, …),
  `worker.py`, `studio/` (backend + `web/` editor, plain ES modules, no build step).
* `pip install -e ".[worker,dev]" && pytest`
* `PLAN.md` – method and measurements; `EDITOR_PLAN.md` – Studio design and status.
* The Windows installer is built by `.github/workflows/windows-installer.yml`
  (PyInstaller + Inno Setup); tag `vX.Y.Z` to publish a release.
