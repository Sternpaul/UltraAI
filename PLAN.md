# UltraAI — lyrics-first UltraStar generator: plan

Goal: given **audio + correct lyrics**, produce a karaoke `.txt` whose words are 100 % right and land at the right time, with sensible screen lines. Pitch should be good, but it's secondary.

## 1. Why UltraSinger's output is never quite right

From reading its code (`ultrastar_writer.py`, `midi_creator.py`, `hyphenation.py`):

| Stage | What UltraSinger does | Consequence |
|---|---|---|
| Lyrics | Whisper *transcribes* the vocals | Misheard and hallucinated words, missing ad-libs and repeats. No way to supply your own lyrics; the only text input is re-pitching an existing txt. |
| Syllables | pyphen splits the word, then the word's time is **divided evenly** between syllables | Syllables are rarely where they're sung, which matters most on held notes. |
| Notes | one note per syllable, pitch = most frequent pitch inside it | Melismas (one syllable over several notes, `~`) are impossible, and slides smear. |
| Line breaks | a `-` wherever the silence between words is longer than the *mean* silence | Lines ignore the real lyric lines: random line lengths, breaks mid-phrase. |
| BPM / grid | detected tempo × a multiplier until it's ≥ 400; GAP = first word | The grid isn't phase-aligned to the music, so the editor view is messy and rounding drifts. |
| Format | defaults to "1.2.0", a version that doesn't exist in the spec | Some readers may warn or reject the file. |

The core (lyrics → syllable timing → notes → lines) is exactly the part we'd replace. The rest (Demucs call, yt-dlp, MusicBrainz lookup) is thin glue.

**Recommendation: write it from scratch** as a small, modular Python package. Borrow ideas freely, but don't fork.

## 2. Core idea

```
lyrics (truth) ──► normalise ──► expected word/syllable sequence + line structure
audio ──► vocal separation ──► forced alignment of the *known* text  ──► word/char times
                           └─► pitch track (f0 + voicing) ─────────────► syllable → note(s)
                           └─► beat/tempo tracking ────────────────────► BPM + GAP grid
                                                           all ─► quantise ─► lines ─► .txt
```

Forced alignment (where is this *known* text sung?) is a far easier and more reliable problem than transcription (what is being sung?). Lyrics stay untouched, so they cannot be wrong; only their timing can be.

## 3. Pipeline in detail

### 3.1 Inputs
- Audio file (mp3/m4a/flac/…). Optionally an existing txt to re-time, or a YouTube URL later.
- Lyrics, in order of preference:
  1. A user-supplied `.txt`/`.lrc`.
  2. **LRCLIB** (free, no API key). It often has *line-synced* LRC, which gives line boundaries and rough line times as anchors.
  3. Other sources (Genius etc.), as plain text.
- Metadata (artist/title/year/genre/cover) from tags or MusicBrainz.

### 3.2 Lyrics normalisation (underrated, a big source of errors)
- Strip section markers (`[Chorus]`, `(x2)`).
- Expand repeats ("Chorus x2", "repeat chorus").
- Keep each line as a screen-line candidate.
- Handle the fact that lyric sites often omit repeated choruses, ad-libs and "oh oh":
  - Run a fast Whisper pass *only as a structural check*.
  - Sequence-align its words to the lyrics (Needleman-Wunsch on phonetic or normalised tokens).
  - Detect lines that are sung but missing (insert a repeat), or written but not sung (drop them, or mark them as freestyle).
  - Ambiguous cases get flagged for review rather than guessed.
- Normalise numbers and symbols only for the aligner. The *displayed* text stays as written.

### 3.3 Vocal separation
- Mel-Band RoFormer / BS-RoFormer via `audio-separator` gives the cleanest vocals (best SDR today). `htdemucs_ft` is the fallback.
- Save the vocals and the instrumental, then reference them with `#VOCALS`/`#INSTRUMENTAL`. That's free "real karaoke" mode in new games.
- Optional: de-reverb / backing-vocal removal model, which helps alignment on dense pop mixes.

### 3.4 Alignment: the heart of the project
- **Candidate aligners**, picked by measurement (see §5), not by opinion:
  - CTC forced alignment with wav2vec2/MMS (`torchaudio` MMS_FA, multilingual, character level). This is what whisperX uses.
  - `stable-ts` `model.align(audio, text)`: Whisper-based alignment of given text. Robust on singing.
  - A singing-specific lyrics aligner trained on DALI, if one is usable.
- **Hierarchical**:
  1. Coarse line windows from LRC timestamps, or from the structural Whisper pass.
  2. Word/char alignment inside each window. This stops errors spreading, and repeated choruses can't get confused.
- **Confidence** per word and line, from CTC posterior and agreement between aligners. Low-confidence lines are interpolated between confident neighbours and flagged.
- Snap word onsets to vocal onsets nearby (energy/spectral-flux on the vocal stem, ±80 ms).

### 3.5 Syllables and notes (where "~" comes from)
- Hyphenation:
  - pyphen per language (German/English/…), plus an exception dictionary.
  - Optional: phoneme-based syllabification (via a g2p) for English, where dictionary hyphenation ≠ sung syllables ("ev-ery" → 2–3).
- Syllable timing comes from the **character-level alignment**: each syllable starts at its first char's time, refined to the nearest onset or pitch change. No more even splits.
- Pitch:
  - RMVPE / SwiftF0 / CREPE on the vocal stem: f0 plus voicing per 10 ms.
  - Inside each syllable, segment the f0 into stable regions.
  - One region → one note. Several clearly different stable regions → first note plus `~` continuation notes (melisma).
  - Slides go to the destination note.
  - Octave-error smoothing.
  - Optional key-snapping.
  - Global octave shift so the song sits in a sensible range.
- Spoken/rapped passages (low voicing, unstable f0) → `R`. Ad-libs that aren't in the lyrics → nothing, or `F`.
- Golden notes (optional): long, stable, held notes, mostly in choruses, capped at ~5–10 % of notes.

### 3.6 Grid: BPM and GAP
- Beat tracking (`beat_this` or madmom/librosa). If the tempo is stable: `#BPM = tempo` (16th-note beats) or `2×tempo` for fast vocal lines, with GAP = a downbeat ≤ the first note. The txt then looks like a hand-made one in editors.
- Variable tempo (live, rubato): fixed `#BPM` ≈ 300–400 (40–50 ms per beat), GAP = just before the first note.
- Quantise in **absolute time first**, then round to beats with carry-over so rounding never accumulates. Enforce no overlap and a minimum 1-beat gap where possible.

### 3.7 Screen lines
- Start from the lyric lines, the user's truth.
- Then apply readability rules:
  - Split long lines at the best internal pause or punctuation. Your library's lines are short: median 20 chars / 4 words / 1.7 s, p90 35 chars / 3.3 s (see format notes §7). So most lyric-site lines get split once.
  - Merge 1–2-word fragments with no pause around them.
  - Never break inside a word.
- Place `-` based on the gap g to the next line (measured in your library):
  - g < 1 s: about the midpoint;
  - long g: about 1 s after the last note, never more than 2 s.
  - Enforce the spec rules (not inside a note, never two in a row).

### 3.8 Output
- `#VERSION:1.0.0` (`#MP3` and `#AUDIO` both written), UTF-8, plus stems, cover, and `#PREVIEWSTART`/medley = first chorus.
- A **`--legacy`** flag for unversioned output.
- By-products for free: `.lrc` (word-synced) and `.ass` karaoke subtitles, plus a JSON with confidences.
- A validator that checks our own output (and existing files) against the spec rules.

### 3.9 Review UI (later, but important in practice)
A tiny local web page:
- Plays the audio with the generated lines scrolling, karaoke-style.
- Highlights low-confidence lines.
- Lets you drag a line or word ±100 ms, or move a line break, then re-export.

Five minutes of review on the flagged 5 % beats days of trying to make the AI perfect.

## 4. Tech stack

- Python 3.12 (managed with `uv`; ML wheels lag on 3.14), PyTorch CPU or CUDA.
- Libraries: `audio-separator`, `torchaudio` (MMS_FA), `stable-ts`, `faster-whisper`, `pyphen`, an f0 tracker (RMVPE/SwiftF0/torchcrepe), `beat_this`, `librosa`, `mutagen`, `ffmpeg`.
- Layout:

```
ultraai/
  lyrics/   (fetch, normalise, structure check)
  separate.py
  align/    (ctc.py, stablets.py, ensemble.py)
  syllables.py
  pitch.py
  grid.py
  lines.py
  ultrastar/ (model, reader, writer, validator)
  cli.py
eval/       (benchmark against existing hand-made songs)
```

**Hardware note:** this server is ARM64, 4 cores, **no GPU**. Everything runs on CPU, but expect a few minutes per song for separation (RoFormer is the slowest step) and a bit less for alignment. That's fine for overnight batches. A machine with an NVIDIA GPU would make it ~10× faster.

## 5. Evaluation: the secret weapon

You already have many songs with hand-made txt files. They're a perfect test set:
1. Take N existing songs (audio + txt). Extract their lyrics, and hide the timing.
2. Run our pipeline on audio + lyrics.
3. Compare against the hand-made file:
   - Word-onset error (median, and % within 100 / 250 ms).
   - Syllable onset error.
   - Line-break agreement.
   - Pitch accuracy mod 12, weighted by duration.
   - Score a perfect singer of the reference would get on our file.
4. Every change to aligners or heuristics is judged by these numbers, not by gut feeling.

Hand-made files are noisy too, so look at the distribution, not individual songs.

## 6. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | UltraStar reader/writer/validator + eval harness on ~20 existing songs | Round-trip identical. Metrics script runs. |
| M1 | Baseline: separation + CTC word alignment + even syllables + median pitch + lyric lines | Valid txt plays in USDX. First numbers. |
| M2 | Char-level syllable timing, onset snapping, melisma/`~`, beat-grid BPM | Clear metric gains over M1 and over UltraSinger on the same set. |
| M3 | Lyrics fetching (LRCLIB) + structure check (missing repeats) + confidence flags | Batch mode over a folder: "audio in, song folder out". |
| M4 | Review web UI, LRC/ASS export, golden/rap heuristics | Comfortable day-to-day use. |
| later | Duets (P1/P2 from two lyric voices), YouTube input, GPU acceleration | |

## 7. Decisions (2026-11-21)

- **Runs anywhere.** Use pure Python + `uv`, CPU by default, CUDA used automatically if present. Also ship a Docker image (multi-arch amd64/arm64). Speed doesn't matter much.
- **Languages:** English first, then German and Greek.
  - Hyphenation: pyphen has `en_US`, `de_DE` and `el_GR`.
  - MMS_FA aligns non-Latin scripts through romanisation (uroman), so Greek works with the same aligner.
  - Greek is the stress test for the eval set.
- **Target game: UltraStar Deluxe (newest) only.** Write `#VERSION:1.0.0` + `#MP3` + `#AUDIO`, and validate against USDX's own parser behaviour (USong.pas).
  - Verified in USDX master `src/base/USong.pas`: it only distinguishes `< 1.0.0` from `≥ 1.0.0`. Any `1.x.x` is treated identically; `≥ 2.0.0` is **rejected** ("Maximum supported version is 1.X.X").
  - `≥ 1.0.0` forces UTF-8, ignores `#ENCODING`/`#RELATIVE`/`#NOTESGAP`/`#DUETSINGERP*`, enables `#AUDIO` (which wins over `#MP3`) and `#TAGS`.
  - `#BPM` and `#START` go through `StrToFloatI18n`, so `,` and `.` both work.
- **Test library** (`songs/`, 33 songs, all working in USDX):
  - 18 are `#VERSION:1.0.0` (usdb_syncer downloads), 15 unversioned.
  - Every file uses `#MP3`; none uses `#AUDIO`.
  - 4 files are CP1252/Latin-1, not UTF-8, so the reader must fall back.
  - BPM ranges 223–408, with both `,` and `.` as decimal separator.
- **Lyrics are fetched automatically:** LRCLIB first (often line-synced), then fallbacks.
- **Library on another server.** Code is developed portable; a sample of ~20–30 songs (audio + txt) is copied here for the benchmark, *or* the dev environment moves there. Pending.

### 7.1 Different audio versions (YouTube vs Spotify vs album)

The txt is always aligned against **the exact audio file that ships in the song folder**, so its timing is correct by construction. Other versions only matter as *hints* or for the *video*:

1. **LRC timestamps are anchors, never truth.**
   - LRCLIB returns a `duration`. Prefer the LRC whose duration matches our audio (±2 s).
   - Before using an LRC, fit offset (and tempo-scale) between its line times and a quick alignment of our audio, and reject outliers.
   - If the fit is bad (different edit: extra intro, cut verse, radio edit), drop the times and keep only the line structure.
2. **Video from YouTube, audio from elsewhere.**
   - Compute `#VIDEOGAP` automatically by cross-correlating the video's own soundtrack with our audio (onset/chroma envelope).
   - If the edits differ (sections added or removed), warn. Optionally use the video's audio as the song audio instead.
3. **Re-timing an existing txt onto a different version:** chroma-DTW between the two recordings maps each beat. This is also useful for fixing old library songs whose mp3 no longer matches.

## 8. Open questions

1. **Library server:**
   - How do we reach it (SSH host from here?).
   - What hardware is it (CPU arch, RAM, GPU)?
   - Copy a sample here, or develop there?
2. **USDX version** you run. Recent builds understand `#VERSION`/`#AUDIO`; very old ones only `#MP3`, which we write anyway.

## 9. Progress log

### M0 (done)
Reader/writer/validator and eval harness. 33 + 2 Greek songs round-trip losslessly; library conventions measured (format notes §7).

### M1 (done)
Separation (HTDemucs for development, Mel-Band RoFormer for production) → MMS_FA forced alignment → SwiftF0 pitch → one note per syllable.
"Take on Me": 52 ms median word-onset error vs 14 s for the naive baseline.

### M2 (in progress, 2026-11-21): measured on the songs finished so far (htdemucs, 6–8 songs)

| Change | Effect (words within 250 ms unless noted) |
|---|---|
| Failure analysis | Most bad words are **gross errors > 1 s** (alignment drifting through intros/backing vocals). Low CTC confidence predicts them: conf ≥ 0.2 → < 1 % gross errors, conf < 0.1 → 24 %. |
| Anchor + window re-alignment without LRC | no gain (bad stretches have no anchors inside) |
| **LRCLIB lyrics + line-time anchors** (own constrained CTC Viterbi, ±1.5 s per line, per-line offset fitted to our audio) | 83.5 % → **89.1 %**, using *fetched* lyrics. All 35 songs have synced LRCs; 27 match the audio duration within 2 s. |
| Screen-line splitting (≤ 40 chars / 5 s at the best pause or punctuation) | line count matches the hand-made files (416 vs 422), line F1 ≈ 77 % |
| Melisma `~` notes from stable pitch regions (≥ 0.15 s) | pitch class accuracy 63.2 → 64.9 %, ~10 % `~` notes like the library |
| Lead-vocal isolation (UVR MDX Karaoke 2 on the vocal stem) | Dancing Queen 78.8 → 82.3 %, Take on Me 81.8 → 86.4 % (+5 min CPU per song) |
| Tempo-locked #BPM/#GAP (librosa beats) | cosmetic, no metric change; librosa's tempo is often 1–3 % off → better beat tracker needed |

Notes:
- Hand-made references carry their own constant offsets (Mamma Mia ≈ +160 ms, Holland ≈ −70 ms). The eval now also reports `onset_rel_*` (constant offset removed) and `constant_offset_ms`.
- Remaining errors: missing chorus repeats in the LRC text (→ structure check), long held falsetto notes, very dense backing vocals.

#### M2, continued (same day)
| Change | Effect |
|---|---|
| Bug fix: lines fully in "(...)" were dropped as section headers | Head Over Heels lyric recall 85 → 98 % |
| Drop "(...)" echoes that repeat the line ("Take on me (take on me)"); strip a repeated tail from backing lines | Take on Me 82.6 → 87.1 % within 250 ms |
| Syllables: no vowel-less parts ("s-ince", "y-ou"), English silent e ("ti-mes", "lo-ved"), sung boundaries ("mo-ney", "ne-ver", "sing-ing") | syllable split agreement 43 → **74 %**, syllable timing 78 → 69 ms |
| Hyphen chants "Ah-ah-ah" → separate words | |
| Golden notes: long steady notes in repeated (chorus) lines, ≤ 4 % | 0.4–7 % per song, like the library |
| Freestyle: syllables with < 10 % voiced frames | 3 % overall; also a useful "misaligned" signal |
| Vocable-only lines ("ah ah ah") that land where nobody sings are dropped | removes instrumental riffs written into LRCs |
| `ultraai batch IN OUT`: tags (+ YouTube junk cleanup, filename/folder fallback), sidecar .lrc/.txt > LRCLIB > embedded lyrics, language detection (en/de/el + Greeklish), Greek → Greeklish by default (`--greek-script` keeps Greek letters), cover from tags, [VOC]/[INSTR] mp3 stems, resumable report.csv with low-confidence line counts | tested on 2 songs |

### Full-library result (35 songs, HTDemucs, 2026-11-21)

| metric | M1 (ref lyrics) | M2 (LRCLIB lyrics) |
|---|---|---|
| onset median | 106 ms | 71 ms |
| onset within 100 ms | 62.4% | 65.4% |
| onset within 250 ms | 83.6% | 86.3% |
| onset within 100 ms (offset-corrected) | – | 74.9% |
| syllable split agreement | 36.7% | 73.7% |
| line F1 | (ref lines) | 83.7% |
| pitch class accuracy | 64.1% | 68.9% |
| word recall vs ref | 99.8% | 90.9% (lyrics differ from refs) |

Regressions to investigate: Taylor Swift - I Knew You Were Trouble (78→64%), Opa (Greek, recall 46%), Der Himmel brennt (99→91%), Ghostbusters (88→82%).

## 10. Toward "nearly perfect": redesign (2026-11-21)

### Where the errors are (M2, 35 songs, 9,587 words, time-aware scoring)

| word start error | share | main cause |
|---|---|---|
| > 1 s | 6.4% | lyrics don't match what is sung (missing chorus repeats/lines), wrong LRC timestamps |
| 0.25–1 s | 6.0% | local slips: backing vocals, ad-libs, long melismas |
| 0.1–0.25 s | 12.2% | precision of CTC boundaries |
| ≤ 0.1 s | 75.4% | (offset-corrected) |

The hand-made references themselves differ by ±50 ms and by per-song offsets of −93…+159 ms,
so "perfect" is measured against noisy truth; > 1 s errors are what ruin a song.

Findings from the regressions:
- Scoring bug: plain LCS word matching paired repeated choruses wrongly (−51 s "errors").
  Fixed: monotonic matching that prefers time-near matches. Ghostbusters/Trouble were mostly this.
- Opa / Trouble: LRCLIB text writes a chorus once where it is sung twice, or drops a line.
- Der Himmel brennt: LRC timestamps wrong by up to 18 s; hard ±1.5 s windows enforced them.
  Patch: `repair_anchors` overrides a timestamp when free alignment disagrees by > 3 s with
  conf ≥ 0.15 (safe: small gains, no losses). Lower thresholds fix Himmel but break others.
- Whisper large-v3-turbo (int8, CPU, ~3 min/song) transcribed Opa's vocals with the correct
  structure incl. the doubled chorus → it can tell us what is actually sung.

### Redesign: listen → read → align → learn

1. **Listen.** Whisper transcript of the (lead) vocals: what is sung, in which order, roughly when.
2. **Read (reconcile).** Collect all lyric candidates (all LRCLIB versions, sidecar, tags). Align each
   to the transcript phonetically. Build the *sung script*: official spelling, but the transcript's
   order and repeats; LRC times only where the transcript agrees. Pick the best candidate by fit.
3. **Align.** One global forced alignment with soft time priors; two independent timings
   (MMS char CTC, Whisper word times) — disagreement = flag.
4. **Learn from the user's library.** Hand-made UltraStar songs + audio are labelled training data:
   a small CPU-trainable onset-refinement net (mel patch around each syllable start → correction),
   learned line-break and golden-note style; later possibly fine-tune the CTC model on singing.
5. **Better lead-vocal separation** (MelBand-RoFormer karaoke model instead of kara2) against
   backing-vocal slips.
6. **Self-check.** Per-song trust score; only flagged lines go to the review page.

Order: 1+2 first (kills > 1 s errors), then 5, then 4 once more library songs are available.

### Languages (2026-11-21)

- Library sample: 291 songs in `library/` (225 en, 37 de, 6 fr, es, it, nl, pt, fi, sv, ko-romanized; 4 duets).
  `songs/` stays the fixed 35-song benchmark.
- `#LANGUAGE` names → ISO codes for ~60 languages (English, native and German names); unknown → `xx`.
- Syllables: pyphen for every language it ships (45), per-language vowel groups, French mute-e rule,
  `qu` as consonant. Agreement with hand-made splits (word level, `scripts/syllable_eval.py`):
  en 94.0, de 95.6, es 75→93, fr 79→91, nl 81→95, it 78→88, sv 84, pt 87, fi 62 (todo).
- Any script: uroman romanization for the aligner (MMS_FA was trained on uroman); Hangul / kana / CJK
  split one character per syllable. Display text keeps the original script.
- Language detection from lyrics: script + stop words for en/de/el/fr/es/it/nl/pt (97% on 320 songs);
  to be replaced by Whisper's language detection once ASR is in the pipeline.

### M3: listen first (2026-11-21)

- `ultraai/transcribe.py`: Whisper large-v3-turbo (faster-whisper, int8, CPU), cached per vocals file.
  ~1–2 min/song. Fails on some rock vocals (Thunderstruck: nothing; Wasted Love: 19 words).
- `ultraai/structure.py`: DP aligning transcript words to lyric lines; cheap deletions (Whisper misses
  a lot), backward jumps only (repeats need positive evidence), lines after the last heard word kept.
  Dropping "unheard" lines was tried and hurt (recall 92 → 89): unheard ≠ not sung.
- `generate_sung` (M3): fetch up to 6 LRCLIB versions, pick the one that fits the transcript best,
  use the sung structure; fall back to M2 when the transcript is too thin (coverage < 50% or < 30% of
  lyric words heard).  Batch converter uses M3 by default.
- First benchmark (35 songs, no-drop): within 250 ms 87.4 → 87.6. Big wins where lyrics/timestamps were
  wrong (Opa 87 → 98, Der Himmel brennt 91 → 99, Dancing Queen 80 → 85); losses where Whisper timing
  was used for held notes / echoes (Take on Me 88 → 77, Just Like Fire 86 → 80).
  → `_merge_lrc_times`: transcript decides structure, LRC timestamp wins where they agree (< 2.5 s).

### Unattended library run

- `systemd --user` service `ultraai-library` in `ultraai.slice` (CPUQuota 320% = 80% of 4 cores, shared
  by all heavy jobs).  `scripts/process_library.py`: random fixed order, resumable, M2 + M3 per song,
  results in `out/library/results.jsonl`, deletes mix/instrumental wavs after each song.
- Status: `.venv/bin/python scripts/library_status.py`, log `out/library/progress.log`.
  ~14 min/song → ~3 days for 294 songs.

### Library run, day 2 (2026-11-22): M3 fixes

First 56 scored library songs: M3 was no better than M2 (85.5 vs 85.6 % words within 250 ms,
3 songs with shifted references excluded).  Wins came from picking a better lyric version;
losses from Whisper words matched to the *wrong repetition* of a line (Agnes - Release Me
-22 points).  Fixes:

- time-aware structure match: with synced lyrics, a transcript word matched far (> 3 s) from
  its line's LRC time costs extra (capped), so repeated text goes to the nearer occurrence
- robust LRC↔transcript offset (consensus of well-heard once-sung lines, not a plain median);
  LRCs off by > 1 s (other release) are shifted into audio time
- lines heard by one stray word count as unheard; weakly heard extra repeats are dropped
- transcript-verified times are used as anchors even when the free alignment cannot confirm them
- LRC lines timed after the end of the audio are dropped (otherwise anchoring fails → unanchored)
- lyrics lookup: looser LRCLIB queries (full artist, first artist, title without brackets,
  title only + artist/duration check); CP1252 files mislabelled UTF-8 decode correctly

Result: library M3 85.5 → 86.4 (M2 85.6), all previous M3 losers recovered; benchmark M3
87.9 → 88.2 (Take on Me +7.6, Happy New Year +4.5).
Remaining big error sources: lyrics that lack sung lines (LRCLIB incomplete), repetitive
phrases ("right round round round") placed on the wrong repeat, choir/rock vocals Whisper
cannot hear, shifted reference files.

Fast experiment loop (no separation needed, ~4 s/song from cache):
    systemd-run --user --slice=ultraai.slice --wait --pipe -p WorkingDirectory=$PWD \
        .venv/bin/python -u scripts/regen_library.py TAG m2,m3
    .venv/bin/python scripts/compare_runs.py base:m2 TAG:m3
    .venv/bin/python scripts/m3_debug.py "song"      # M3 decisions per line

### Notes: repeats, golden notes, melisma (2026-11-23)

- Hand-made library files: 79 % of repeated lines (same words, same note count) have
  identical pitches (per-song median 84 %), so repeats should match.
- Harmonizing repeats with the median of per-line pitches *lowered* pitch-class accuracy
  (70.5 -> 66.5 %, 55 songs); copying melisma shapes was the worst part. Pooling the sung
  pitch frames of all repeats and taking the most common semitone keeps accuracy
  (70.5 -> 70.7 %, 25 songs) and makes repeats identical. Repeats that disagree
  (key change, variation) are not changed but become "repeat" review issues.
- Golden notes: notes reached by a pitch jump >= 4 semitones (octave jumps treated as tracker
  errors) scored by jump + distance from the line's range, >= 0.3 s, voiced; top 4 %, and
  the same positions in every repeat. (Old rule: long steady notes in repeated lines.)
- Melisma: long wobbly syllables no longer give up above 4 pitch steps; short steps are
  merged until at most 6 remain.
