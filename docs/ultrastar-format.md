# UltraStar .txt format — working notes

Sources: the official spec repo (github.com/UltraStar-Deluxe/format: *Unversioned*, *v1*, *v2 draft*, VERSIONING.md), usdx.eu/format, and UltraSinger's writer. Things marked **(convention)** come from community practice, not the spec.

## 1. Versions

| Version | Status | What matters for us |
|---|---|---|
| unversioned (no `#VERSION`) | archived, what most old songs use | `#MP3`, optional `#ENCODING`, `#RELATIVE`, `#DUETSINGERP1/2`, only P1/P2 |
| **1.0.0** (= 1.1.0, "for historical reasons equivalent") | published Dec 2023, current | adds `#VERSION`, `#AUDIO` (replaces `#MP3`), `#VOCALS`, `#INSTRUMENTAL`, `#TAGS`, `#PROVIDEDBY`, `#RENDITION`, `*URL` headers, P1..P9; must be UTF-8 |
| 1.2.0 | **not in the spec**; UltraSinger uses it as its default anyway | avoid |
| 2.0.0 | unpublished draft, breaking | all times in integer ms, `#MEDLEYSTART/END` in ms, BPM decimal point only, `-` beat optional; do not emit yet |

Readers should reject files whose *major* version they don't know. So write `#VERSION:1.0.0`, never 2.x.

## 2. Timing model (the most important part)

- `#BPM` is "implicitly quadrupled": one beat in the file lasts `60 / (4 × BPM)` seconds.
  - `#BPM:300` → 1200 beats/min → 50 ms per beat.
  - If the song tempo is *T* quarter notes per minute, writing `#BPM:T` makes one beat = one 16th note. `#BPM:2T` gives 32nd-note resolution. That's why community files often show `#BPM` ≈ 1–2× the real tempo.
- `#GAP` = ms from the start of the audio to beat 0.
- Time of beat *b* = `GAP/1000 + b × 60 / (4 × BPM)` seconds.
- Decimal separator in v1 may be `,` or `.`; old files mostly use `,`. Emitting `.` is valid v1; for maximum old-game compatibility we can emit `,` (decide after testing in USDX).
- Units that trip people up (v1 / unversioned):
  - `#GAP` is ms.
  - `#VIDEOGAP` is seconds (may be negative).
  - `#START` is seconds.
  - `#END` is ms.
  - `#PREVIEWSTART` is seconds.
  - `#MEDLEYSTARTBEAT/ENDBEAT` are beats.
  - (v2 turns all of these into ms.)
- `#START`/`#END` only cut playback; they don't move notes.

## 3. Headers we will write

```
#VERSION:1.0.0
#TITLE:...           #ARTIST:...
#LANGUAGE:German     (English names of languages, comma-separated)
#YEAR:  #GENRE:  #EDITION:  #CREATOR:
#MP3:Song.mp3        (still written for old games, which only know MP3)
#AUDIO:Song.mp3      (v1 readers prefer AUDIO when present)
#VOCALS:Song [VOC].mp3        #INSTRUMENTAL:Song [INSTR].mp3
#COVER:Song [CO].jpg          #BACKGROUND:Song [BG].jpg     #VIDEO / #VIDEOGAP
#BPM:...   #GAP:...
#PREVIEWSTART:...  #MEDLEYSTARTBEAT / #MEDLEYENDBEAT  (chorus = medley/preview)
```

Rules:
- Header keys are case-insensitive. Unknown keys are ignored; app-specific keys use the form `#APP-KEY`.
- Encoding is UTF-8 without BOM; never write `#ENCODING`.
- Paths are relative.
- Deprecated, don't emit: `#RELATIVE`, `#ENCODING`, `#DUETSINGERP*`, `#AUTHOR`, `#RESOLUTION`, `#NOTESGAP`, `#PREVIEW`.

## 4. Body

```
: <start> <dur> <pitch> <text>     normal note
* ...                              golden (bonus points)
F ...                              freestyle (not scored, pitch ignored)
R ...                              rap (timing scored, pitch ignored)
G ...                              golden rap
- <beat>                           end of phrase = next "screen"/line
P1 / P2 ...                        voice switch (duets), needs #P1:/#P2: names
E                                  end of file
```

- Pitch is semitones relative to C4. 0 = C4 = MIDI 60, so MIDI = pitch + 60. Many games score modulo 12, so octave errors cost little. Absurd octaves still look bad, though.
- Text holds one syllable. The word separator is a space at the **end** of the last syllable (`Hel` `lo ` `world `) or at the start of the next syllable. Both are valid; we consistently use trailing spaces. **(convention)** Most hand-made files do the same.
- **(convention)** `~` as the text means "same syllable continues on a new pitch" (melisma): `: 10 3 5 ba` `: 13 2 7 ~` `: 15 4 4 ~ `. It's not in the spec, but USDX, Vocaluxe and editors all render it.
- Notes must not overlap. Start beats rise within a voice.
- `-` must not fall inside a note, must not be first or last, and two `-` lines must never follow each other. The line's beat is the moment the *next* line takes over the screen. **(convention)** Place it shortly after the last note of the line ends: halfway into short gaps, a few beats after the note for long gaps.
- Relative mode (`- a b`, `#RELATIVE:yes`) is legacy. We read it, but never write it.

## 5. Line ("screen") layout — **(convention)**, not spec

- A line ≈ one lyric line: ~3–10 words, ≲ 45–50 characters, ≲ 6–8 s. USDX shrinks long lines, which become hard to read.
- Break at natural lyric line boundaries and at pauses. Never break inside a word.
- A very long held note at a line's end is fine. A tiny 1–2-word line is usually merged with a neighbour unless a long pause surrounds it.

## 6. Game differences

| Game | Notes |
|---|---|
| UltraStar Deluxe (USDX) | reference implementation; supports golden, rap, duets P1/P2, medley, `#VERSION` (since 2024 builds) |
| Vocaluxe | txt; treats `R`/`G` as freestyle |
| UltraStar Play / Melody Mania | txt; no medley; P1/P2 OK |
| Performous | reads UltraStar txt (+ its own/SingStar/Frets formats); many headers "unknown" in support tables |
| UltraStar WorldParty | fork of USDX; declined v1 headers, so old-style `#MP3` matters |
| Yass / Karedi | editors; useful to inspect output by hand |
| SingStar (PS) | XML melody files, a different world; out of scope |
| Plain karaoke (KaraFun, CDG, `.ass`/`.lrc`) | no pitch; we can export LRC/ASS as a free by-product of the alignment |

Compatibility recipe:
- v1.0.0 file plus `#MP3` (as well as `#AUDIO`).
- Integer beats and `-` with one parameter.
- No relative mode, UTF-8.
- Only `:`, `*`, `F`, plus `R` where clearly rapped.

## 7. Measured in the test library (33 songs, 13,685 notes — `scripts/survey_library.py`)

These numbers replace the guesses in §5 and calibrate the generator.

| What | Median | p10–p90 | Takeaway |
|---|---|---|---|
| beat length | 50 ms | 39–64 ms | `#BPM` ≈ 240–380 (≈ tempo, or 2× tempo) |
| note length | 169 ms | 76–402 ms | |
| gap between notes | 2 beats / 104 ms | 1–9 beats | notes almost never touch (3.5 %) |
| **line length** | **20 chars, 4 words, 1.7 s** | 7–35 chars, 1–7 words, 0.8–3.3 s | screen lines are **half** a typical lyric-site line, so split |
| syllables per word | 1 | 1–2 | |
| notes per syllable | 1.10 | 1.01–1.27 | ~10 % of notes are `~` melisma notes |
| golden notes | 4 % | 2–7 % | |
| freestyle `F` | 4.5 % of notes | | no `R`/`G` in this set |
| pitch range | 17 semitones | 12–27 | |
| `-` position, gap < 1 s | 59 % into the gap | | ≈ middle |
| `-` position, gap ≥ 2 s | 0.96 s after the line ends | 0.1–2.0 s, **never > 2 s** | the editor rule is apparently "≤ 2 s after the last note" |

Word separator: trailing space 68 %, leading space 32 %. Both are common, and the reader handles both.
Languages: 28 English, 5 German. **No Greek songs yet**, and no duets.
