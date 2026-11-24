# Roadmap — approved improvement PRs (2026-11-24)

From a full review of the repository (pipeline, Studio, worker, scripts, tests, packaging).
Every item below is **one separate pull request** so each can be reviewed and merged on its own.
Benchmarks (A1/A2/A4/A5) are deliberately deferred: the current benchmark run is a one-time
thing and is running on the server right now. Duets (B6) are parked.

Approved scope: **A3, B1–B5, C, D, E** (see the status table).

## How we work

- One branch per PR off `main`: `pr/<nn>-<slug>`, PR title `PR-<nn>: <title>`.
- Labels: `docs`, `ci`, `refactor`, `tests`, `chore`, `feature`, `security`.
- Every PR uses the PR template (added in PR-01) — it lists the definition of done below.
- Pipeline-affecting PRs (PR-08 … PR-13) must include measured numbers in `PLAN.md`
  (progress log) using the fast loop:

  ```bash
  systemd-run --user --slice=ultraai.slice --wait --pipe -p WorkingDirectory=$PWD \
      .venv/bin/python -u scripts/regen_library.py TAG m2,m3
  .venv/bin/python scripts/compare_runs.py base:m2 TAG:m3
  ```

  plus a full library run (`scripts/process_library.py`) for anything that lands on `main`.
- No PR may regress `onset_within_250ms` / `pitch_class_acc` on the 35-song benchmark
  (`songs/`), and must not slow the library run beyond its measured rate.

## Status of the original proposals

| Item | Proposal | Status |
|---|---|---|
| A1/A2/A4/A5 | benchmark songs/spread/live bench file/Pareto/interactions | deferred (one-time bench running now) |
| A3 | per-stage timings in `bench_models.py` | **PR-07** |
| B1 | onset snapping (±80 ms) | **PR-08** |
| B2 | Whisper word-level priors in anchored Viterbi | **PR-09** |
| B3 | auto-correct when ≥2 double-check models agree | **PR-10** |
| B4 | better beat tracking + grid metrics | **PR-11** |
| B5a | by-products: word-synced `.lrc`, `.ass`, `#PREVIEWSTART` (first chorus) | **PR-12** |
| B5b | `#VIDEOGAP` cross-correlation, key snap, octave range | **PR-13** |
| B6 | duets | parked |
| C | Studio API hardening | **PR-14** (approved) |
| D1 | central config registry | **PR-03** |
| D2 | core-pipeline tests + CI test workflow | **PR-02 + PR-04** |
| D3 | small fixes (report.csv, cache guards, LRCLIB TTL, …) | **PR-05** |
| D4 | Docker image for the worker | **PR-06** |
| E | CONTRIBUTING.md, README architecture, PR template, roadmap | **PR-01** |
| — | repo security checker (CodeQL, secret scanning, Dependabot, pip-audit, gitleaks, SECURITY.md, branch protection, make repo public) | **PR-02** |

## PR plan

Order matters: PR-01/02 first (docs + CI so every later PR is tested), PR-03 before the
feature PRs (they add config options), then the rest roughly in listed order.

| # | Branch | Title | Depends on |
|---|---|---|---|
| 01 | `pr/01-docs` | docs: CONTRIBUTING, README architecture, PR template, roadmap | — |
| 02 | `pr/02-ci-security` | ci: tests + pip-audit + gitleaks, CodeQL, Dependabot, SECURITY.md, repo goes public, branch protection on main | — |
| 03 | `pr/03-config-registry` | refactor: central config registry for all `ULTRAAI_*` options | — |
| 04 | `pr/04-core-tests` | tests: align/generate/tempo/lrc/audio/config with synthetic fixtures (no model downloads, no network) | 03 |
| 05 | `pr/05-small-fixes` | chore: report.csv append, mix.wav/beats cache guard, LRCLIB TTL+rate limit, library-run failure backoff, one greeklish, `python -m scripts.*`, ruff+pre-commit | 03 |
| 06 | `pr/06-docker` | feat: multi-arch Docker image for the worker + CI build | 02 |
| 07 | `pr/07-bench-stage-times` | feat: per-stage timings in the model benchmark (A3) | 03 |
| 08 | `pr/08-onset-snapping` | feat: snap word/syllable onsets to vocal onsets ±80 ms (B1) | 03, 04 |
| 09 | `pr/09-whisper-priors` | feat: Whisper word-time priors in the anchored Viterbi (B2) | 03, 04, 08 |
| 10 | `pr/10-check-consensus` | feat: double-check models auto-correct on 2+ agreement (B3) | 03, 04 |
| 11 | `pr/11-beat-tracking` | feat: more robust beat tracking + grid phase/tempo metrics (B4) | 03, 04 |
| 12 | `pr/12-song-byproducts` | feat: word-synced .lrc + .ass export + #PREVIEWSTART = first chorus (B5a) | 03 |
| 13 | `pr/13-videogap-keysnap` | feat: #VIDEOGAP via audio cross-correlation, optional key snap, octave range (B5b) | 03, 12 |
| 14 | `pr/14-studio-security` | security: Studio API hardening (C) | 02 |

---

## PR-01 — docs: CONTRIBUTING, README architecture, PR template, roadmap

**Files:** `CONTRIBUTING.md` (new), `README.md` (architecture diagram + link), `.github/PULL_REQUEST_TEMPLATE.md` (new), `docs/ROADMAP.md` (this file).

**Content of CONTRIBUTING.md:** dev setup (`pip install -e ".[worker,dev]"`, `pytest`),
server setup, the fast experiment loop (`regen_library.py` → `compare_runs.py` → `m3_debug.py`,
~4 s/song from cache), the full library run, benchmark scripts, how to add a config option
(after PR-03: registry entry), how to add a model (registry + bench), PR checklist.

**README:** mermaid diagram of the pipeline (audio → separate → analyse → listen/reconcile →
align → notes → grid → txt) and of the Studio split (laptop ↔ SSH ↔ server), plus the
cache layout. Keep the existing text; add the diagrams in a "How it works" section.

**PR template checklist:** tests pass (`pytest`), lint clean (ruff, after PR-05), docs updated,
for pipeline PRs: PLAN.md progress entry with `compare_runs.py` numbers, no benchmark regression,
worker still runs with base install only (no heavy imports in `ultraai/studio`).

## PR-02 — ci: tests + security scanning, repo goes public

**Files:** `.github/workflows/tests.yml`, `.github/workflows/codeql.yml`,
`.github/workflows/gitleaks.yml`, `.github/dependabot.yml`, `SECURITY.md`,
`.github/workflows/docker.yml` is PR-06.

- `tests.yml`: on PRs + pushes to main; ubuntu-latest, Python 3.11 + 3.12 matrix,
  `pip install -e .[dev]`, `pytest -q`, then `pip-audit` (dependency vulnerabilities —
  works on private repos too, so it stays even if visibility changes).
- `codeql.yml`: CodeQL, languages `python` + `javascript`, on PRs + main (free on public repos).
- `gitleaks.yml`: secret scanning in CI (works everywhere, belt and braces next to GitHub's own).
- `dependabot.yml`: pip (root `pyproject.toml`) + github-actions, weekly.
- `SECURITY.md`: what to do with a finding; note the Studio binds 127.0.0.1 only and that
  after PR-14 mutations need a per-install token; report via GitHub private vulnerability reporting.
- Repo settings (via `gh api` when hooked up): make the repo **public** (README already links
  the public releases page), enable secret scanning + code scanning + Dependabot alerts,
  branch protection on `main`: require 1 approving review, require `tests` status check,
  no force pushes. Labels: the ones listed above.

## PR-03 — refactor: central config registry (D1)

**Problem:** ~30 `ULTRAAI_*` env vars are read at import time via `__import__("os").environ`
in `generate.py`, `worker.py`, `structure.py`, `pitch.py` — undiscoverable, and tests become
order-dependent.

**Plan:** new `ultraai/config.py` with a registry:

```python
@dataclass(frozen=True)
class Opt:
    env: str            # ULTRAAI_...
    default: str
    doc: str

REGISTRY: dict[str, Opt] = {...}   # every option, with its doc string

def get(name: str) -> str: ...     # reads os.environ at call time, falls back to default
```

- Migrate every `os.environ.get("ULTRAAI_...")` call site to `config.get(...)` (env names stay,
  so the server setup and scripts keep working).
- `ultraai config list` prints a table (name, default, doc) — also used by `docs` and tests.
- `worker.apply_options` maps job.json options onto the same registry.
- Tests: `tests/test_config.py` — default, env override, unknown option raises.

## PR-04 — tests: core pipeline with synthetic fixtures (D2)

**Problem:** `align.py`, `generate.py` core, `pitch.py`, `audio.py`, `lrc.py`, `tempo.py`,
`batch.py` have no tests; the `songs/`-dependent tests silently skip in a fresh clone.

**Plan:** new tests that need **no model downloads and no network**:

- `tests/test_align.py`: the custom constrained `viterbi` (`align.py:201`) is pure numpy —
  build a synthetic emission matrix where the correct path is known, assert the spans;
  plus `lrc_offsets` / `repair_anchors` with synthetic `AlignedWord`s.
- `tests/test_generate_notes.py`: `build_syllables`, `_segments` (melisma), `split_lines`,
  `harmonize_repeats`, `mark_note_kinds`, `to_song` with a synthetic `PitchTrack` (numpy arrays).
- `tests/test_tempo.py`: `choose_grid` steady/unstady/fallback branches with synthetic beats.
- `tests/test_lrc.py`: cache naming + TTL (PR-05) with monkeypatched HTTP.
- `tests/test_audio.py`: `file_hash`, `work_dir` layout, cache-hit paths with a fake Separator.
- `tests/test_config.py` (PR-03), plus keep `test_studio_doc.py` round-trip as is.
- `conftest.py`: helpers to build a tiny synthetic vocal wav (sum of sines with gaps) so a
  smoke test can run `analyse`-adjacent pure functions end to end.

## PR-05 — chore: small fixes (D3)

- `batch.py:329`: append to `report.csv` instead of rewriting all rows per song (O(n²) → O(1)).
- `generate.py:958-963`: don't re-run ffmpeg when only `mix.beats.npz` is needed.
- `lrc.py:70-88`: single cache naming scheme, TTL (e.g. 30 d) + polite rate limit for LRCLIB.
- `scripts/process_library.py:96-99`: backoff instead of aborting after 5 consecutive failures.
- Unify the two Greek transliterations (`lyrics.py:29` vs `batch.py:124`) into one function.
- `scripts/`: runnable as `python -m scripts.x` (no `sys.path` hacks).
- `pyproject.toml`: ruff config; `.pre-commit-config.yaml` (ruff + trailing whitespace + no
  large files); CI lint job folded into `tests.yml`.

## PR-06 — feat: Docker image for the worker (D4)

- `Dockerfile` (worker): `python:3.12-slim` + ffmpeg, `pip install ".[worker]"`,
  `ENTRYPOINT ["ultraai"]`, volume for `~/.cache/ultraai`, docs in README server section.
- `.github/workflows/docker.yml`: `docker buildx` multi-arch (amd64, arm64) on release tags,
  push to ghcr.io, plus a PR job that only builds (no push) to keep PRs green.

## PR-07 — feat: per-stage timings in the model benchmark (A3)

- `generate.py`: lightweight stage timer (context manager, active when
  `ULTRAAI_STAGE_TIMING=1`) around separate / analyse / transcribe / align / notes.
- `scripts/bench_models.py`: set it, record `stages: {separate: s, ...}` per row in
  `results.jsonl` next to total `seconds`.
- `scripts/bench_report.py`: per-config mean per stage.
- `scripts/bench_export.py`: write `stage_min` into `model_bench.json`; `models.rate()` uses it
  when present so the Studio ETA and the worker's learned `stage_rates.json` improve from bench
  runs too. Benchmarks themselves stay as they are (one-time run in progress).

## PR-08 — feat: onset snapping (B1)

PLAN §3.4 promises "snap word onsets to vocal onsets nearby (±80 ms)"; it is not implemented.

**Plan:** new `ultraai/onsets.py`: spectral-flux onset envelope of the 16 kHz vocal stem
(`librosa.onset.onset_strength`, hop 160 = 10 ms), peak picking, cached next to the vocals as
`vocals.{sep}.onsets.npz`. In `generate_from_lines` after `build_syllables`: snap each syllable
start to the nearest onset peak within ±80 ms (never across the previous syllable's start),
config `ULTRAAI_ONSET_SNAP` (default on once measured; snap amount also recorded per word in
the sidecar for debugging). Word starts (not just syllables) get snapped first, syllables follow
their word.

**Acceptance:** `regen_library.py` + `compare_runs.py` on the 35-song benchmark: median onset
error and `onset_rel_within_100ms` improve, `onset_within_250ms` not worse; PLAN.md entry;
unit test with a synthetic wave (tone bursts at known times).

## PR-09 — feat: Whisper word-time priors in the anchored Viterbi (B2)

`align_anchored` constrains lines to LRC windows only; the Whisper transcript already provides
per-word times (used in `_merge_lrc_times`).

**Plan:** extend `viterbi()` with an optional per-target time prior: for each word, a Gaussian
bonus `beta * exp(-(t - t_asr)^2 / 2σ^2)` added to the emission scores of that word's char
states (σ ≈ 0.3 s, β ≈ 1.0, only where Whisper coverage ≥ threshold and the word was heard).
Wire through `align_anchored(..., asr_words=...)` from `generate_from_lines`. Config
`ULTRAAI_ASR_PRIOR` (β; 0 = off).

**Acceptance:** same measurement loop as PR-08; expect gains on the 0.25–1 s local-slip bucket
(6 % of words). Unit test: synthetic emissions where the prior flips the argmax path.

## PR-10 — feat: double-check consensus auto-correct (B3)

Today `double_check_issues` (`generate.py:797`) only *flags* disagreements. With ≥2 compare
models chosen, agreement of the checks against each other is strong evidence.

**Plan:** when ≥2 pitch-check models disagree with the main model by ≥2 semitones (octave-folded)
*and agree with each other within 1 semitone*, set the syllable pitch to the consensus and emit
the issue as kind `auto-fixed` (lower risk, reason says what was applied). Same for separation
checks: if ≥2 separations show ≥15 dB less energy on a syllable than the main one, mark it
unvoiced (→ freestyle candidate) and flag. Only active when >1 compare model is configured
(B3 condition). Config `ULTRAAI_CHECK_AUTOFIX=1`.

**Acceptance:** benchmark numbers + a test that a 2-vs-1 disagreement is corrected and a 1-vs-1
is still only flagged.

## PR-11 — feat: better beat tracking + grid metrics (B4)

PLAN notes librosa's tempo is 1–3 % off; `tempo.py` uses only `librosa.beat.beat_track`.

**Plan:** two-stage tempo: librosa tempo as initialisation, then refine on the onset envelope
with a tempogram + global least-squares beat-grid fit over all detected beats (the polyfit in
`choose_grid` already exists — extend it to choose among tempo × {1, 2, 1/2} by best steady fit).
Optional `beat_this`/`madmom` only behind an extra, not a hard dependency. Add grid metrics to
`eval/metrics.compare()`: BPM ratio and GAP phase error vs the reference (so grid changes are
measurable, today they are cosmetic).

**Acceptance:** `test_tempo.py` cases; `compare()` reports `grid_bpm_ratio` /
`grid_phase_ms`; benchmark shows no regression and the grid metric improves on songs where
librosa was off.

## PR-12 — feat: song by-products (B5a)

- Word-synced `.lrc` writer (from the sidecar `words`): `[mm:ss.xx]word` per word, one line per
  screen line — written next to the txt by `ultraai batch`/worker as `<name>.words.lrc`.
- `.ass` karaoke export (Syllable `\k` tags) — `<name>.ass`.
- `#PREVIEWSTART`: first chorus = the repeated-line group (`_line_groups`, `generate.py:233`)
  with the most occurrences, first occurring after ~15 % of the song; write
  `#PREVIEWSTART:<ms>` (format 1.0.0 supports it; USDX shows it as the preview start).
  Config `ULTRAAI_PREVIEWSTART=1` (default on after measuring).
- Studio: the song's "…" menu gets "Copy .lrc / .ass" (files already sit next to the txt).

**Acceptance:** `ultraai check` passes on generated files with the new header; writer/reader
round-trip test; PLAN.md entry.

## PR-13 — feat: #VIDEOGAP, key snap, octave range (B5b)

- `#VIDEOGAP`: when video and audio come from different sources, cross-correlate onset
  envelopes (chroma/onset) of the video's own audio (ffmpeg decode) vs the song audio
  (PLAN §7.1.2) → offset in ms; if the envelopes don't align well (different edit), warn in
  the sidecar instead of writing a wrong gap. Studio writes it when video exists and source
  differs; today it hardcodes `VIDEOGAP:0` (correct only for same-upload).
- Optional key snap (`ULTRAAI_KEYSNAP=0` default): estimate key from the pitch histogram
  (Krumhansl–Schmuckler profiles), snap notes ≤1 semitone toward the scale — measure first.
- Octave range: global shift by whole octaves so the median note sits in a singable range
  (`ULTRAAI_OCTAVE_RANGE=55:79`, default on if it doesn't change pitch-class metrics).

**Acceptance:** unit tests with synthetic pitch histograms; benchmark shows pitch metrics
unchanged; VIDEOGAP tested with a synthetic shifted audio pair.

## PR-14 — security: Studio API hardening (C)

All in `ultraai/studio/`:

1. Random per-install token (generated on first run, stored in the config dir, mode 600)
   replaces the constant `X-UltraAI: 1`; compared with `hmac.compare_digest`; the UI fetches it
   from a new `GET /api/token` (localhost-only) — or simpler: the token is printed into the
   page by the server-side template. **Chosen approach:** server injects the token into
   `index.html` at serve time (no extra endpoint), `api.js` reads it from a meta tag.
2. `Host` header must be `127.0.0.1:<port>` / `localhost` (blocks DNS rebinding); keep the
   127.0.0.1 bind.
3. Sanitize the audio suffix against `AUDIO_EXT` whitelist; `shlex.quote` all remote paths in
   `processors.py` (`REMOTE_START`, `check()`, job paths).
4. Atomic writes (tmp + `os.replace`) for `Settings.save`, `lyrics.txt`, version files;
   version numbering under the library lock (monotonic counter, not `len(glob)`); quarantine a
   corrupt `song.json`/`settings.json` instead of crashing.
5. Optimistic concurrency on `PUT /api/songs/{sid}/doc`: client sends the `saved` timestamp it
   loaded; server 409s on mismatch (reprocess bumps it).
6. Upload size limit (e.g. 2 GiB) on `POST /api/songs/files`; temp files cleaned in `finally`.
7. Job cancellation: `POST /api/songs/{sid}/cancel` → stops the local child / kills the remote
   systemd unit; stale remote `jobs/<id>` dirs reaped after 24 h on `check()`.
8. Tests for all of the above in `tests/test_studio.py` style (TestClient); docs in README
   settings section + SECURITY.md pointer.

---

## GitHub hookup (what I need)

1. Install `gh` (I'll download the release zip into `~/bin` — no admin needed) and
   authenticate with a **fine-grained PAT** created at
   `github.com/settings/personal-access-tokens/new` with access to `Sternpaul/UltraAI`:
   `Metadata: read` (mandatory), `Contents: read+write`, `Pull requests: read+write`,
   `Issues: read+write` (to file this roadmap as issues), `Actions: read` (watch CI),
   `Administration: read+write` (flip visibility, branch protection, enable scanning).
   Save the token to a file outside the repo (e.g. `C:\Users\stern\.vibe\github-token.txt`);
   I'll run `gh auth login --with-token`, then delete the file. Never committed anywhere.
2. Then I can: push branches, open the PRs above, set labels/branch protection, make the
   repo public, enable secret scanning / code scanning / Dependabot (PR-02).
3. Alternative without `gh`: plain `git` over HTTPS with the token as password + the REST
   API via `curl` for PRs/settings — works too, just more verbose; I'll default to `gh`.

## Definition of done (every PR)

- [ ] Branch off `main`, single topic, no unrelated changes.
- [ ] `pytest` green (new tests included), ruff clean.
- [ ] Docs updated (README / PLAN.md / EDITOR_PLAN.md / CONTRIBUTING as applicable).
- [ ] Pipeline PRs: `compare_runs.py` numbers in the PR description + PLAN.md entry.
- [ ] PR description follows the template: what, why, files, how to review, how to test.
- [ ] CI green (tests, lint, pip-audit, CodeQL, gitleaks).
