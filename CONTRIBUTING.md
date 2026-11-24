# Contributing to UltraAI

Thanks for looking at the code. This project is developed measurement-first: every change to
the pipeline is judged by numbers on the benchmark songs, not by gut feeling (see
[PLAN.md](PLAN.md) for the method and the progress log with all measurements).

## Setup

```bash
git clone https://github.com/Sternpaul/UltraAI.git && cd UltraAI
python3 -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"   # worker = server, dev = pytest
.venv/bin/pytest                                                     # tests (no models or songs needed)
# lighter, no torch (what CI uses): pip install -e ".[dev,test]" && pytest   # 29 passed, 3 skipped
```

- The **Studio** (desktop app) needs only the base package: `pip install -e .` — no
  numpy/torch. `ultraai/studio/` must never import the heavy pipeline modules.
- The **processing worker** runs on the Linux server with `.[worker]` (see README).
- The benchmark songs (`songs/`, 35 hand-made UltraStar files with audio) are *not* in the
  repo; tests that need them skip automatically. Ask the maintainer for a copy.

## Repository layout

```
ultraai/            pipeline + worker + Studio
  generate.py       notes/syllables/lines from alignment + pitch (the heart)
  align.py          MMS_FA CTC forced alignment, custom constrained Viterbi
  structure.py      what is actually sung (Whisper transcript vs lyrics, DP)
  lyrics.py lrc.py  lyrics normalisation, LRCLIB fetch + cache
  pitch.py audio.py tempo.py transcribe.py issues.py
  ultrastar/        txt reader/writer/validator/model
  eval/             benchmark harness + metrics (score vs hand-made files)
  worker.py         `ultraai process JOBDIR` (what the Studio runs on the server)
  studio/           FastAPI backend + web editor (plain ES modules, no build step)
scripts/            benchmark / library-run / analysis scripts (run as `python -m scripts.x`)
tests/              pytest; core tests use synthetic fixtures, no downloads
packaging/          Windows installer (Inno Setup + PyInstaller, built by CI on tags)
docs/               ultrastar-format.md, ROADMAP.md
PLAN.md             method + measurements   EDITOR_PLAN.md   Studio design + status
```

Cache of all expensive steps (separations, CTC emissions, pitch tracks, transcripts):
`~/.cache/ultraai/work/<sha1(audio)>/` (override with `ULTRAAI_CACHE`). Delete a folder to
force recomputation of one song.

## The fast experiment loop (~4 s/song, no separation needed, from cache)

```bash
systemd-run --user --slice=ultraai.slice --wait --pipe -p WorkingDirectory=$PWD \
    .venv/bin/python -u scripts/regen_library.py TAG m2,m3
.venv/bin/python scripts/compare_runs.py base:m2 TAG:m3     # metric table
.venv/bin/python scripts/m3_debug.py "song name"             # M3 decisions per line
```

Full library run (hours, resumable, one song at a time):
`scripts/process_library.py`, status via `scripts/library_status.py`.

Model benchmarks (separation/pitch/lead models on a few songs, resumable):
`scripts/bench_models.py` → `scripts/bench_report.py` → `scripts/bench_export.py`.

## Configuration options

All `ULTRAAI_*` environment variables are registered in `ultraai/config.py` with a default
and a doc string (see PR-03). List them with:

```bash
ultraai config list
```

The Studio passes per-song options through `job.json` (`worker.apply_options`); scripts set
env vars. Never read `os.environ` at import time — read via `config.get("NAME")` at call time
so tests stay order-independent.

## Adding a model (separation / pitch / lead)

1. Add the key + display name to the registry in `ultraai/models.py` (and the model file to
   `SEPARATION_MODELS`/`LEAD_MODELS` in `ultraai/audio.py` if it needs a file name).
2. Add a config to `scripts/bench_models.py`, run the benchmark, export with
   `scripts/bench_export.py` so `model_bench.json` carries measured scores + speed.
3. The Studio model picker picks it up automatically from `/api/models`.

## Pull requests

- One PR per topic, branched off `main` as `pr/<NN>-<slug>` (the roadmap in
  [docs/ROADMAP.md](docs/ROADMAP.md) numbers the planned ones).
- Use the PR template: what/why, files, how to test, and the checklist.
- Labels: `docs`, `ci`, `refactor`, `tests`, `chore`, `feature`, `security`.
- Pipeline-affecting PRs must include `compare_runs.py` numbers in the PR description and a
  new entry in the `PLAN.md` progress log; no regression on `onset_within_250ms` /
  `pitch_class_acc` on the 35-song benchmark.
- CI must be green: `tests` (pytest + pip-audit), CodeQL, gitleaks.
- `main` is protected: one approving review required, no force pushes.
