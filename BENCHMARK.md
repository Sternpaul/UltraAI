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
| melband (Kim RoFormer) | **92.0** | 80.8 | 80.2 | 87.6 | 992 |
| MelBandRoformerBigSYHFTV1 | 91.8 | 79.6 | 78.9 | 86.8 | 1758 |
| melband_roformer_big_beta4 | 91.3 | 80.6 | 79.6 | 87.2 | 3147 |
| melband_roformer_big_beta5e | 91.2 | **81.4** | 81.6 | 88.6 | 2536 |
| mel_band_roformer_kim_ft_unwa | 90.7 | 79.0 | 79.5 | 87.2 | 7458 |
| UVR-MDX-NET-Voc_FT.onnx | 90.6 | 80.0 | 80.8 | 88.7 | **427** |
| model_mel_band_roformer_ep_3005_sdr_11.4360 | 90.1 | 79.1 | 81.3 | 88.8 | 5612 |
| **Kim_Vocal_2.onnx** | 90.0 | 78.3 | **82.5** | **90.0** | **427** |
| MDX23C-8KFFT-InstVoc_HQ | 90.0 | 79.2 | 81.0 | 88.9 | 7082 |
| UVR-MDX-NET_Main_406.onnx | 89.8 | 79.1 | 80.4 | 88.5 | 426 |
| htdemucs_ft | 88.2 | 76.4 | 81.4 | 88.2 | 1468 |
| htdemucs (default) | 87.6 | 76.7 | 80.1 | 88.4 | ~cached |
| hdemucs_mmi.yaml | 86.8 | 74.6 | 80.5 | 88.1 | 330 |
| model_bs_roformer_ep_368_sdr_12.9628 | *(running, 1 of 3 songs)* | | | | ~7700 |

Per song the ranking moves around: on the hardest of the three songs (lowest scores for every
model) BS-RoFormer ep_368 has the best timing of all models (87.8 vs 86.2 for melband Kim),
at ~8× melband's time. Its full 3-song result follows when the run finishes.

**Not worth their time** (slower *and* worse than melband Kim on these songs): MDX23C,
mel_band_roformer ep_3005 (viperx), mel_band_roformer_kim_ft_unwa, big_beta4.

**Dropped from the long run** to save ~20 h: BS-RoFormer ep_317 (near twin of ep_368, same
architecture, SDR 12.98 vs 12.96) and all karaoke/lead models (see the karaoke section below).

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

* The separation ranking rests on 3 songs only, and the pitch ranking showed that 3 songs can
  mislead (rmvpe won on 3, swift on 25). The 25-song separation depth pass decides the default.
* **Best quality:** melband Kim RoFormer separation (timing 92.0) + rmvpe pitch — but ~16 min/song on the bench host.
* **Best balance:** **Kim_Vocal_2.onnx** separation (timing 90.0 *and* best pitch 82.5, ~7 min/song) + **swift** pitch (best exact pitch on the 25-song set, 71.2; use rmvpe if ±1 semitone matters more, 82.9).
* **Fastest reasonable:** htdemucs (default) + swift — 87.6 timing, 80.1 pitch, separation cached.
* Karaoke/lead models: measured on 25 songs — clear negative (see above), stay off.

## In progress / planned

| Run | Scope | Status |
|---|---|---|
| Long model benchmark (low-power host) | 22 configs × 3 songs (lead models and BS ep_317 dropped); 20 done | BS-RoFormer ep_368, InstVoc Duality v2 left (~6–8 h) |
| Separation depth pass (low-power host) | htdemucs + Kim_Vocal_2 + Voc_FT + melband × **25 songs** (language/genre-stratified, same selection logic as `bench_cache_fill.py`) | queued after the long run (~10 h) |
| Pitch depth pass (desktop CPU) | 6 pitch models × **25 songs** | **done — table above** |
| Lead/karaoke depth pass | 10 lead models × 25 songs | **dropped — KARA_2 already shows a clear negative; see above** |
| Parameter sweeps | alignment windows, note styles, harmonize tolerances (each ~7 min on 25 songs) | planned |
| Alternative timing approach | Whisper word-timestamps instead of forced alignment ("m4") | planned |

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
