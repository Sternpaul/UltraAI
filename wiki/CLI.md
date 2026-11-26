# Command line

```
ultraai studio [--no-window]   run UltraAI Studio (any OS; --no-window uses the browser only)
ultraai process JOBDIR         process one job folder (what the Studio runs on the server)
ultraai batch [options] DIR    convert a folder of audio files into USDX folders (+ report.csv)
ultraai make AUDIO LYRICS      generate one song from an audio file and a lyrics file
ultraai eval [options]         score generated songs against hand-made references
ultraai check FILE.txt         validate an UltraStar file (prints warnings/errors)
```

Useful environment (all registered in `ultraai/config.py`, see
[Configuration]](Configuration)): `ULTRAAI_SEPARATION`, `ULTRAAI_PITCH`, `ULTRAAI_LEAD`,
`ULTRAAI_PITCH_CHECK`, `ULTRAAI_SEP_CHECK`, `ULTRAAI_CACHE`, `ULTRAAI_COOKIES`,
`ULTRAAI_MAX_JOBS`.

## Batch

`ultraai batch music/ out/` reads every audio file in `music/`, finds lyrics (sidecar →
LRCLIB → embedded tags → YouTube captions as flagged last resort), processes and writes
`out/<Artist - Title>/` with the `.txt`, audio copy, cover, vocals/instrumental, a
`.ultraai.json` sidecar (issues, words, per-line info) and optional `.lrc`/`.ass` exports.
`report.csv` is resumable — re-running skips finished songs unless `--force`.

## Eval / benchmarks

`ultraai eval` and `scripts/bench_*.py` / `scripts/regen_library.py` score against hand-made
references using `ultraai.eval.metrics.compare` (timing, pitch, word recall, line F1). The
curated results live in [BENCHMARK.md](https://github.com/Sternpaul/UltraAI/blob/main/BENCHMARK.md); the quick PR gate is
`scripts/bench_pr.sh <branch>` on a machine that has the benchmark library and the analysis
cache (see BENCHMARK.md → Reproduce).
