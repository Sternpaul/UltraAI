# Automatic pipeline — improvement research

How to make the automatic (no-human) path of `ultraai batch` / Studio jobs produce better
`.txt` files, based on the benchmark evidence in [BENCHMARK.md](../BENCHMARK.md) (25-song
cached gate set, 20/25 English, matching the real library mix), the PR stack, and the
architecture exploration. Every quality claim below is benchmark-backed unless marked
"idea" (needs a gate run before enabling).

Pipeline today: tags → lyrics (sidecar > LRCLIB > tags > YouTube subs, last resort) →
separate → ASR (Whisper) → MMS_FA emissions → structure DP → align (Viterbi, optional
anchors + ASR prior) → syllables/lines → issues → tempo grid → USDX write.

## 1. Lyrics acquisition — biggest quality lever

- **Multi-source consensus (idea, high impact).** When LRCLIB returns several versions and
  a YouTube video id is known, score every candidate with `yt_agreement` (Whisper-vs-source
  word agreement) and let the transcript pick the best candidate up front, instead of
  relying only on the structure DP. Evidence: agreement spans 0.04–0.84 on the gate set —
  low agreement reliably means garbled auto-captions or a hard song.
- **Auto-flag instead of auto-publish (idea, high impact, cheap).** If the best candidate's
  agreement with the ASR transcript is below a threshold (≈0.5), route the song to
  `needs_review` with the reason instead of writing a confident .txt. Prevents silent
  garbage for songs LRCLIB misses.
- **Better lookup metadata (idea).** Extend `name_candidates` with MusicBrainz/Discogs
  lookups so "feat." variants and localized titles resolve more often; LRCLIB coverage is
  the main miss source for the gate set.
- **Wire `yt_agreement` into the Issues tab** (sidecar already stores it) so a low score
  is visible in the editor as an issue row.

## 2. Alignment / timing

- **Confidence-weighted emissions (idea).** Scale CTC emission log-probs by per-frame
  entropy in the Viterbi, so noisy frames trust the ASR prior more. Gate before enabling.
- **Two-pass alignment (idea).** Free pass → per-line median offsets → second pass
  anchored to those medians. Cheaper and more stable than LRC/sub anchors (per-line sub
  anchors measured −5.7 rel-100ms; section-level priors might still help long gaps —
  needs a gate run).
- Onset snapping is **rejected** (PR-08 closed: all variants regressed rel-100ms at n=25).

## 3. Pitch

- Defaults from the 25-song depth pass: `swift` (best exact pitch 71.2, 31 s/song);
  `rmvpe` best ±1 semitone (82.9) at +12 s/song. Check-autofix stays **off**
  (sweeps −0.2…−0.6).
- **Confidence-gated ensemble (idea).** Run swift everywhere; run rmvpe only on frames
  where swift's confidence is low, and take rmvpe's pitch there. Could capture most of
  rmvpe's ±1 robustness at a fraction of its cost. Gate before enabling.
- `keysnap` measured +0.2 pitch on the gate with timing flat — keep off by default, but
  surface as a one-click editor action.

## 4. Structure / repeats

- **Adaptive `struct_drop` (idea).** Use ASR coverage + agreement to decide per song
  whether weakly-heard extra repeats are dropped (currently a global off).
- **Intro/outro guard (idea).** Use the pitch loudness track to suppress false lines in
  instrumental intros before the structure DP.

## 5. Tempo / grid

- `beat_robust` (onset-scored grid, PR-11) and `#VIDEOGAP` (PR-13) are on; both gated 0.0.
- Little left here; swing/shuffle detection is the only open idea and likely marginal for
  this library.

## 6. Speed / cost

- Separation dominates wall-clock. The queued separation depth pass on 25 songs decides
  the default: fastest model within ~1 pt of the best timing (candidates from the long
  run: Kim_Vocal_2 ≈90 timing at ~7 min/song vs htdemucs 87.6 baseline).
- `stage_timing` (PR-07) already records per-stage seconds — use it to auto-pick models
  per machine speed in the Studio picker.
- **Parallel song workers (idea).** `worker.lock` serializes all jobs on one machine;
  shard the lock by audio hash so N songs process concurrently when CPU allows.
- Batch cache fills already shard (`bench_cache_fill.py --shard i/k`).

## 7. Robustness / engineering

- Migrate all remaining scattered `ULTRAAI_*` env reads to the config registry (PR-03).
- `structure.match` and `metrics.match_words` keep full O(N·M) Python matrices — switch
  to rolling rows / numpy; matters on long songs.
- Studio: replace 2–3 s polling with SSE for progress/logs; cache `scan()` results with
  mtime invalidation; add a version list/restore API (versions are written but not
  exposed); finish retargeting the PR stack to main after the CI/security PR merges.
- Fault-injection tests for error paths (ffmpeg failure, corrupt cache, empty audio,
  LRCLIB down) — currently untested almost everywhere.

## 8. Benchmark infrastructure

- Grow the gate set to 50–100 songs, English-weighted automatically
  (`bench_cache_fill.py` keeps true library proportions since 03f5bda); add small
  per-language gates for the other languages in the library.
- Keep `model_bench.json` history so model regressions are visible over time.
- Every .txt-affecting change runs the local gate (`scripts/bench_pr.sh`) and pastes the
  summary table into the PR — keep enforcing this.

## Suggested priority (impact × effort)

| # | item | impact | effort |
|---|------|--------|--------|
| 1 | auto-flag low-agreement songs to review | high | low |
| 2 | candidate consensus via `yt_agreement` | high | medium |
| 3 | separation depth-pass default pick | high | low (data pending) |
| 4 | confidence-gated swift+rmvpe ensemble | medium | medium |
| 5 | parallel song workers | medium | medium |
| 6 | O(N·M) DP memory fixes | low–medium | low |
| 7 | Studio SSE + scan caching + version restore | medium (UX) | medium |
| 8 | fault-injection tests | medium (safety) | medium |
