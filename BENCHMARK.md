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
| melband_roformer_big_beta4 | 91.3 | 80.6 | 79.6 | 87.2 | 3147 |
| melband_roformer_big_beta5e | 91.2 | **81.4** | 81.6 | 88.6 | 2536 |
| UVR-MDX-NET-Voc_FT.onnx | 90.6 | 80.0 | 80.8 | 88.7 | **427** |
| model_mel_band_roformer_ep_3005_sdr_11.4360 | 90.1 | 79.1 | 81.3 | 88.8 | 5612 |
| **Kim_Vocal_2.onnx** | 90.0 | 78.3 | **82.5** | **90.0** | **427** |
| MDX23C-8KFFT-InstVoc_HQ | 90.0 | 79.2 | 81.0 | 88.9 | 7082 |
| UVR-MDX-NET_Main_406.onnx | 89.8 | 79.1 | 80.4 | 88.5 | 426 |
| htdemucs_ft | 88.2 | 76.4 | 81.4 | 88.2 | 1468 |
| htdemucs (default) | 87.6 | 76.7 | 80.1 | 88.4 | ~cached |
| hdemucs_mmi.yaml | 86.8 | 74.6 | 80.5 | 88.1 | 330 |

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

## Pipeline feature gate (25 reference songs, generator m3)

Every pipeline change is gated before merging: regenerate all 25 cached songs and require
**no regression** in timing / timing-rel / pitch vs the base. Base (m3): **87.2 / 77.2 / 69.5**.
Recent results: Whisper-prior alignment, double-check auto-correct, robust beat grid,
song byproducts (.lrc/.ass/#PREVIEWSTART), #VIDEOGAP/key-snap — all **0.0 delta, pass**.
Onset-snapping to spectral-flux peaks was tested in 7 variants and **closed**: it adds
jitter (timing-rel −1.0) without improving timing. Details on the pull requests.

## Current recommendations

* **Best quality:** melband Kim RoFormer separation (timing 92.0) + rmvpe pitch — but ~16 min/song on the bench host.
* **Best balance:** **Kim_Vocal_2.onnx** separation (timing 90.0 *and* best pitch 82.5, ~7 min/song) + **rmvpe** pitch (+15 s/song). This is the pick for quality without a long wait.
* **Fastest reasonable:** htdemucs (default) + swift — 87.6 timing, 80.1 pitch, separation cached.
* Karaoke/lead models: 10 candidates queued in the long run — no numbers yet.

## In progress / planned

| Run | Scope | Status |
|---|---|---|
| Long model benchmark (low-power host) | 31 configs × 3 songs; 17 done | ~14 configs left |
| Depth pass on this machine | all 7 pitch models × **25 songs**, incl. desktop-CPU s/song | running |
| Lead/karaoke depth pass | 10 lead models × song subset | planned |
| Separation depth pass | top-3 separation models × 10 songs | planned |
| Parameter sweeps | alignment windows, note styles, harmonize tolerances (each ~7 min on 25 songs) | planned |
| Alternative timing approach | Whisper word-timestamps instead of forced alignment ("m4") | planned |

## Reproduce

```bash
python scripts/bench_models.py out/bench        # model benchmark (resumable, cached per song)
python scripts/bench_report.py out/bench        # summary table
bash scripts/bench_pr.sh <branch>               # PR gate: regenerate cached songs, compare vs base
python scripts/bench_cache_fill.py library 25   # grow the cached benchmark set (genre/language stratified)
```

The benchmark songs are not in this repo. The analysis cache is keyed by the sha1 of the
audio file, so a cache + identical audio transfers between machines and just hits.

> Note on times: s/song above is measured on the low-power host of the long run; desktop
> CPUs are several times faster. The 25-song depth pass records desktop times per model,
> which is what the Studio model picker will show.
