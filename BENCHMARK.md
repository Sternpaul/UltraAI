# Benchmarks — which models are best?

UltraAI's quality depends on three model choices: **separation** (vocals out of the mix),
**pitch tracking** (the melody) and an optional **karaoke/lead** model (strip backing vocals).
This page records what we measured so the defaults and the Studio model picker rest on data,
not gut feeling.

All scores come from `ultraai.eval.metrics.compare` against hand-made UltraStar reference
files, averaged over the benchmark songs:

| Score | Meaning |
|---|---|
| **timing** (`onset_within_250ms`) | % of notes that start within 250 ms of the reference — the main quality number |
| **timing-rel** (`onset_rel_within_100ms`) | same, after removing a constant offset — pure jitter between consecutive notes |
| **pitch** (`pitch_class_acc`) | % of notes with the correct pitch class (octave-free) |
| **pitch±1** (`pitch_class_acc_1`) | % within one semitone — "close enough to sing" |
| **s/song** | wall-clock seconds per song for that model (see note) |

> Song names are deliberately not published (the benchmark library is private). Only
> aggregated means are shown. Comparisons are only valid **within** a table — different
> song sets have different difficulty (the 3-song set scores higher than the 25-song set).

## Separation models (3 reference songs, pitch = SwiftF0)

| Model | timing | timing-rel | pitch | pitch±1 | s/song |
|---|---|---|---|---|---|
| model_bs_roformer_ep_368_sdr_12.9628 | **92.2** | 81.4 | 80.2 | 87.1 | 7933 |
| melband_roformer_instvox_duality_v2 | **92.2** | **81.7** | 79.6 | 87.1 | 1677 |
| melband (Kim RoFormer) | 92.0 | 80.8 | 80.2 | 87.6 | 992 |
| MelBandRoformerBigSYHFTV1 | 91.8 | 79.6 | 78.9 | 86.8 | 1758 |
| melband_roformer_big_beta4 | 91.3 | 80.6 | 79.6 | 87.2 | 3147 |
| melband_roformer_big_beta5e | 91.2 | 81.4 | 81.6 | 88.6 | 2536 |
| mel_band_roformer_kim_ft_unwa | 90.7 | 79.0 | 79.5 | 87.2 | 7458 |
| UVR-MDX-NET-Voc_FT.onnx | 90.6 | 80.0 | 80.8 | 88.7 | **427** |
| model_mel_band_roformer_ep_3005_sdr_11.4360 | 90.1 | 79.1 | 81.3 | 88.8 | 5612 |
| **Kim_Vocal_2.onnx** | 90.0 | 78.3 | **82.5** | **90.0** | **427** |
| MDX23C-8KFFT-InstVoc_HQ | 90.0 | 79.2 | 81.0 | 88.9 | 7082 |
| UVR-MDX-NET_Main_406.onnx | 89.8 | 79.1 | 80.4 | 88.5 | 426 |
| htdemucs_ft | 88.2 | 76.4 | 81.4 | 88.2 | 1468 |
| htdemucs (default) | 87.6 | 76.7 | 80.1 | 88.4 | ~cached |
| hdemucs_mmi.yaml | 86.8 | 74.6 | 80.5 | 88.1 | 330 |

Per song the ranking moves around: on the hardest of the three songs (lowest scores for every
model) BS-RoFormer ep_368 has the best timing of all models (87.8 vs 86.2 for melband Kim),
at ~8× melband's time. Over all three songs it and InstVoc Duality v2 lead by 0.2 — well inside
the noise of 3 songs (see the depth pass below: the 3-song order did not hold on 23 songs).

**Not worth their time** (slower *and* worse than melband Kim on these songs): MDX23C,
mel_band_roformer ep_3005 (viperx), mel_band_roformer_kim_ft_unwa, big_beta4.

**Dropped from the long run** to save ~20 h: BS-RoFormer ep_317 (near twin of ep_368, same
architecture, SDR 12.98 vs 12.96) and all karaoke/lead models (see the karaoke section below).

## Depth pass: separation models on 25 reference songs (low-power host)

htdemucs (default) and the three cheapest strong models from the table above, on 25
language/genre-stratified library songs; 23 scored (one song has no pitchable notes, one
reference file is shifted against its audio). pitch = SwiftF0; minutes = separation + pipeline
per song on the 4-core ARM host.

| Model | timing | timing-rel | pitch | pitch±1 | min/song | songs better / worse than htdemucs (timing) |
|---|---|---|---|---|---|---|
| UVR-MDX-NET-Voc_FT.onnx | **91.2** | 83.1 | 75.6 | 85.7 | 8 | 12 / 5 (median +0.6) |
| melband (Kim RoFormer) | **91.2** | **83.4** | 75.2 | 85.3 | 27 | 9 / 7 (median 0.0) |
| Kim_Vocal_2.onnx | 90.9 | 83.0 | 75.9 | **86.0** | 8 | 9 / 7 (median 0.0) |
| htdemucs (default) | 90.6 | 82.4 | **76.1** | 85.9 | cached | — |

Lesson: on 23 songs the separation model barely matters — all four within 0.6 timing and
0.9 pitch. The 3-song gaps (melband +4.4 timing over htdemucs, Kim_Vocal_2 +2.4 pitch) were
the songs, not the models. Single songs still swing by up to ±15 points between models, so a
second separation stays useful as a *double-check* (disagreements become review issues), not
as a new default. **htdemucs stays the default**; Voc_FT is the pick for a small timing gain at
the same speed.

## Pitch models (3 reference songs, separation = htdemucs)

| Model | pitch | pitch±1 | extra s/song |
|---|---|---|---|
| **rmvpe** | **82.0** | **91.0** | +15 |
| crepe-full | 81.6 | 88.8 | +633 |
| crepe-tiny | 80.7 | 89.0 | +28 |
| swift (default) | 80.1 | 88.4 | +3 |
| fcpe | 79.9 | 87.9 | +9 |
| vote (multi-model) | 79.9 | 89.2 | +24 |
| pesto | 79.2 | 88.5 | +22 |

Timing is identical across pitch models (they only change note pitches, not onsets).

## Depth pass: pitch models on 25 reference songs (desktop CPU)

The 3-song run above is a small sample; this table is the same 7 pitch models on the full
25-song benchmark set (24 scored; one song has no pitchable notes), with desktop wall-times:

| Model | pitch | pitch±1 | s/song (desktop) |
|---|---|---|---|
| **swift (default)** | **71.2** | 81.8 | 31 |
| crepe-tiny | 70.7 | 82.1 | 118 |
| rmvpe | 70.6 | **82.9** | 43 |
| vote | 70.4 | 81.5 | 28 |
| fcpe | 70.0 | 80.8 | **18** |
| pesto | 68.6 | 80.5 | 61 |
| crepe-full | (73.1 on 3 songs) | (87.2) | 892 — not worth it |

Lesson: the 3-song ranking (rmvpe 82.0 vs swift 80.1) **did not generalize** — on 25 songs
swift has the best exact pitch and rmvpe only wins within a semitone. Keep swift as default;
rmvpe is the pick if ±1 semitone matters more than exact pitch.

## Karaoke/lead models: measured, not worth it (so far)

Backing-vocal removal (ULTRAAI_LEAD) was tested on the 25-song set with UVR_MDXNET_KARA_2:
timing **−1.5**, timing-rel **−2.7**, pitch +0.8 — at a cost of ~+825 s/song. Removing
backing vocals degrades the stem the aligner relies on. **Recommendation: keep lead off**
(the default). The long run no longer ranks the other lead models either; revisit only if a
new karaoke model promises a large pitch gain.

## Pipeline feature gate (25 reference songs, generator m3)

Every pipeline change is gated before merging: regenerate all 25 cached songs and require
**no regression** in timing / timing-rel / pitch vs the base. Base (m3): **87.2 / 77.2 / 69.5**.
Recent results: Whisper-prior alignment, double-check auto-correct, robust beat grid,
song byproducts (.lrc/.ass/#PREVIEWSTART), #VIDEOGAP/key-snap — all **0.0 delta, pass**.
Onset-snapping to spectral-flux peaks was tested in 7 variants and **closed**: it adds
jitter (timing-rel −1.0) without improving timing. Details on the pull requests.

## Current recommendations

* 3 songs mislead for both separation and pitch: the 3-song winners (melband, rmvpe) did not
  win on 23–25 songs. Decide on the depth passes only.
* **Default: htdemucs + swift.** Best pitch on 23 songs (76.1), timing within 0.6 of the best,
  fastest.
* **Slightly better timing:** UVR-MDX-NET-Voc_FT.onnx separation (91.2 vs 90.6 on 23 songs, better
  on 12 songs and worse on 5), same speed. Not enough to switch the default.
* **Not worth it on CPU:** melband and all bigger RoFormers — same 23-song result as Voc_FT at
  3× the time; BS-RoFormer ep_368 is ~2 h per song.
* **Pitch:** swift; rmvpe only if ±1 semitone matters more than exact pitch.
* Karaoke/lead models: measured on 25 songs — clear negative (see above), stay off.

## In progress / planned

| Run | Scope | Status |
|---|---|---|
| Long model benchmark (low-power host) | 22 configs × 3 songs (lead models and BS ep_317 dropped) | **done — table above** |
| Separation depth pass (low-power host) | htdemucs + Kim_Vocal_2 + Voc_FT + melband × **25 songs** | **done — separation barely matters, htdemucs stays** |
| Pitch depth pass (desktop CPU) | 6 pitch models × **25 songs** | **done — table above** |
| Lead/karaoke depth pass | 10 lead models × 25 songs | **dropped — KARA_2 already shows a clear negative; see above** |
| Parameter sweeps | harmonize / melisma / minseg / repair / robust / golden / ASR model | **done — table above, defaults win** |
| Alternative timing approach | Whisper word-timestamps instead of forced alignment ("m4") | **done — clearly worse, dead end** |
| Check-model combos (double-check auto-fix) | 5 combos × 25 songs | **done — negative on 25 songs, keep off** |
| YT-subs anchor test | 7 songs with cached subs | **done — negative (line_f1 −16.3), anchor default off** |

## Sweep results (25 reference songs, m3, deltas vs base 87.2 / 77.2 / 69.5)

All parameter sweeps leave timing unchanged — **the current defaults are fine**. Pitch reacts:

| Test | timing | timing-rel | pitch |
|---|---|---|---|
| harmonize off | +0.0 | +0.0 | +0.4 |
| harmonize tol 4 | +0.0 | +0.0 | −0.2 |
| harmonize melisma unify | +0.0 | +0.0 | −1.2 |
| min. segment 0.30 / 0.08 s | +0.0 | +0.0 | −0.7 / −1.5 |
| **melisma off** | +0.0 | +0.0 | **−2.9** |
| repair off | +0.0 | +0.1 | +0.1 |
| robust align | +0.0 | +0.0 | 0.0 |
| golden legacy | +0.0 | +0.0 | 0.0 |
| ASR medium instead of large-v3-turbo | −0.3 | −0.2 | +0.2 |

→ Keep melisma splitting on (it carries real pitch accuracy); keep large-v3-turbo (medium is
a wash at ~4× the transcription cost). Double-check auto-correct combos (rmvpe+fcpe,
rmvpe+crepe-tiny, fcpe+crepe-tiny, +pesto, +crepe-tiny) all *lowered* pitch by 0.2–0.6 on the
25-song set — the +0.6 seen on 3 songs did not generalize → keep `ULTRAAI_CHECK_AUTOFIX` off.

**Alternative timing approach ("m4": Whisper word-timestamps directly as note times, no
forced alignment):** timing 71.1 / timing-rel 61.2 / word_recall 69.8 vs m3's 87.2 / 77.2 /
93.7 on the same 25 songs — clearly worse (Whisper alone misses/repeats words vs the lyrics).
The CTC alignment + lyric matching stays.

**How much can 3 songs mislead?** Leave-one-out CIs on the 3-song long run: melband timing
92.0 [88.5..94.8] vs Kim_Vocal_2 90.0 [86.7..93.8] — fully overlapping, i.e. the 3-song
ranking is not significant. That is why the depth passes on 25 songs decide defaults.
Per-language on the 25-song gate (small n, indicative): English timing 81.5 vs German 91.1 /
Spanish 99.4 — English-language songs are the current weak spot.

## YouTube-subtitle experiments (PR-15/PR-16)

YouTube auto/manual captions were tested as a timed-lyrics source on the songs where subs
could be fetched (n=7 of the 25; YouTube 429-rate-limits subtitle downloads hard — with a
cookies file we still only got 7/25; PO-token support would be the robust fix):

- **As anchors/lyrics in m3: negative.** vs base m3 on the same 7 songs: timing −1.2,
  timing-rel −1.0, pitch −1.5, word_recall −3.9, line_f1 **−16.3** (sub line breaks don't
  match reference phrasing — subs replaced the lyric *text* here). → `ULTRAAI_YT_ANCHOR`
  should default to **0**; subs stay a fallback lyrics source for songs LRCLIB misses.
- **Times-only variant (LRCLIB lyrics + sub-derived anchor times): also negative.**
  line_f1/word_recall recover (−0.3 / 0.0) but timing drops: −3.7 / −4.8 (caption-chunk
  starts ≠ musical line starts; only 3/7 songs engaged the anchored path). → subs are not
  a useful timing source either.
- **Corrected run with exact USDB ytids** (`.usdb` meta_tags `v=`, no search; n=3 with
  usable subs): times-only becomes neutral (timing −0.4, timing-rel **+1.1**, text flat) —
  the earlier timing drop was mostly wrong-video. Full subs as lyrics: catastrophic
  (word_recall −60.8, line_f1 −23.3) even for the exact video. → subs as lyric text: never;
  exact-ytid times-only anchors: neutral, keep `ULTRAAI_YT_ANCHOR` off by default.
- **`yt_agreement`** (Whisper vs YouTube-ASR word agreement) is a useful free quality signal:
  mean 0.57 on the 7 songs, range 0.04–0.84 — garbled auto-captions are detectable this way.

### Follow-up: exact-ytid rerun on the gate set, English-weighted (2026-07)

- The 25-song gate set is **20/25 English**, matching the real library (~80% English);
  `bench_cache_fill.py` now keeps true library language proportions (majority language
  capped at 90%, not 50%) so future fills stay representative.
- Exact USDB ytids (`v=` in `.usdb` meta_tags) were mapped for the gate songs
  (11/25 matched by artist-title; no YouTube search). Fetching subs for the missing
  ones hit the YouTube 429 wall hard (all rate-limited; 3 of 10 had genuinely no
  captions) → only **1 English gate song** had exact ytid + usable subs.
- On that song, times-only anchoring was clearly negative (timing −16.5, timing-rel
  −14.0, `yt_agreement` 0.51) — consistent with the n=7 result above. Combined with the
  exact-ytid USDB n=3 (neutral, +1.1 timing-rel), the verdict stands: **YouTube subs are
  not a useful timing source; `ULTRAAI_YT_ANCHOR` stays 0**. They remain (a) a fallback
  lyrics source and (b) the `yt_agreement` review signal.
- To widen n beyond the 429 wall, PO-token support for yt-dlp is the follow-up
  (cookies alone still got rate-limited here).

### Update: 429 wall lifted via `--impersonate` + `--sleep-subtitles` (PR-17, #20)

The upstream workarounds work: `--impersonate chrome` (curl_cffi) plus
`--sleep-subtitles 60` fetches subtitles reliably again (manual subs landed for 4 of
the exact-ytid gate songs; a few videos genuinely have no captions). Note: per-language
requests can still 429 individually — keep whatever subtitle files landed.

With that, the exact-ytid times-only rerun reached **n=5 English gate songs**
(exact USDB ytids, cached subs): timing **−4.9**, timing-rel **−5.7**, pitch −1.6,
word_recall flat — clearly negative (one song anchored catastrophically). Final verdict:
**`ULTRAAI_YT_ANCHOR` default is now 0** (changed in PR-17). YouTube subs remain
(a) fallback lyrics when LRCLIB misses and (b) the `yt_agreement` review signal.

## Reproduce

```bash
python scripts/bench_models.py out/bench        # model benchmark (resumable, cached per song)
python scripts/bench_report.py out/bench        # summary table
BENCH_SONGS=songs.txt BENCH_CONFIGS=sep:htdemucs,sep:melband \
  python scripts/bench_models.py out/bench-depth  # chosen configs on another song set
bash scripts/bench_queue.sh                     # the server queue: long run, then the depth pass
bash scripts/bench_pr.sh <branch>               # PR gate: regenerate cached songs, compare vs base
python scripts/bench_cache_fill.py library 25   # grow the cached benchmark set (genre/language stratified)
```

The benchmark songs are not in this repo. The analysis cache is keyed by the sha1 of the
audio file, so a cache + identical audio transfers between machines and just hits.

> Note on times: s/song above is measured on the low-power host of the long run; desktop
> CPUs are several times faster. The 25-song depth pass records desktop times per model,
> which is what the Studio model picker will show.
