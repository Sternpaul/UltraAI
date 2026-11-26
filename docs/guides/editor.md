# Editor tutorial

The editor is where you review and fix what the automation produced. Open a song from the
library (songs marked *Needs review* open here first).

## Layout

![UltraAI Studio editor](editor.png)

Top: game-style preview with the current lyrics. Middle: piano roll — notes over the singer's
pitch curve (orange), what speech recognition heard (grey words at the bottom), the vocal
waveform, red areas = review spots, dashed boxes = repeat groups. Right: the Issues / Lines /
Repeats tabs. Bottom: overview strip (click to seek).

## The review workflow

1. The **Issues** tab lists everything the machine is unsure about, each with its **time in
   the song**. Click the ⇥ button on a row (or press `Tab` / `Shift+Tab`) to jump: the
   timeline zooms to the spot, the notes are selected, and playback seeks there.
2. Fix it (see below), then mark it **✓ OK** to dismiss. Save warnings (e.g. overlapping
   notes) also appear here, with a plain-language message.
3. Everything **autosaves**; `Ctrl+S` saves immediately. Every save is a version — *Versions*
   on the song card lists them and restores any with one click.

## Mouse

- Click a note to select (Shift: add to selection). Drag to move in time/pitch; drag the edges
  to resize. Drag the ▼ markers to move line breaks.
- Double-click a note to edit its syllable; `Enter` edits the whole line's text
  (`|` separates syllables: `Re|lease my bo|dy`).
- `Ctrl`+wheel or `+`/`-` zooms, wheel scrolls, the overview strip seeks.

## Keyboard

| Key | Action |
|---|---|
| `Space` | play / pause |
| `←` `→` | nudge selection in time (Shift: bigger step) |
| `↑` `↓` | pitch ±1 semitone (Shift: octave) |
| `Delete` / `Backspace` | delete selected notes |
| `Enter` | edit current line's text |
| `S` / `M` | split note / merge with next |
| `N` | selected: make normal note · nothing selected: new note at mouse/playhead |
| `G` `F` `R` | golden / freestyle / rap note (press again: back to normal) |
| `B` / `Shift+B` | start a new line at selection / join with next line |
| `C` | copy this line's notes to all its repeats ("Make same") |
| `K` | link/unlink repeats (edits then apply to all copies) |
| `L` | loop selection / current issue |
| `V` | vocals only / full mix |
| `[` `]` | slower / faster playback |
| `T` | tap mode: play the line, hold any letter key per syllable to re-time it |
| `P` | set selection to the sung pitch |
| `Tab` / `Shift+Tab` | next / previous issue |
| `Ctrl+Z` / `Ctrl+Y` | undo / redo |
| `Ctrl+S` | save now |
| `Esc` | clear selection / leave tap mode |
| `Home` | back to song start |
| `?` | the built-in shortcut list |

## Tabs on the right

- **Issues** — review list (above). Red ▲ = lyric words missing in the song, dashed notes =
  sung words not in the lyrics, plus pitch/structure/validator warnings.
- **Lines** — the lyrics text per screen line; edit here to fix wrong words.
- **Repeats** — groups of repeated lines; link them (`K`) so fixing one fixes all copies.

## Toolbar

★ Golden notes… re-places golden notes (jumps / long holds / highest notes / mix).
*USDB* shows the txt ready for usdb.animux.de (the `#VIDEO` tags with the YouTube id and
cover link) — copy or save it.

Tip: the orange pitch curve is what the pitch model heard; grey words under it are what
speech recognition heard. If they disagree with a note, that note is usually wrong — `P`
fixes it to the sung pitch.
