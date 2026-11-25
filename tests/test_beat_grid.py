"""tempo.py robust beat-grid choice + eval grid metrics (PR-11 / B4).

Pure numpy: no audio, no librosa.  Local helpers only (no conftest on this base).
"""

import numpy as np
import pytest

from ultraai.eval.metrics import compare
from ultraai.tempo import (
    FALLBACK_BPM,
    PRIOR_PENALTY_S,
    Grid,
    best_phase,
    bpm_prior_penalty,
    choose_grid,
    choose_grid_robust,
    score_candidate,
)
from ultraai.ultrastar.reader import parse

# ---- (a) candidate scoring ----------------------------------------------------


def _best_candidate(onsets, bpms, strengths=None):
    """(bpm, score) of the best-scoring candidate, phase optimised per candidate."""
    onsets = np.asarray(onsets, dtype=float)
    scored = []
    for bpm in bpms:
        phase = best_phase(onsets, bpm, strengths)
        scored.append((score_candidate(onsets, bpm, phase, strengths,
                                       t0=float(onsets[0]), t1=float(onsets[-1])), bpm))
    return min(scored)[1], min(scored)[0]


def test_scoring_picks_true_grid_among_half_double_octaves():
    # true grid: 120 BPM (pulse 125 ms), onsets on beats and off-beats (eighths)
    p = 60.0 / (4 * 120)
    onsets = [0.37 + k * p for k in range(40)] + [0.37 + (k + 0.5) * p for k in range(40)]
    best, _ = _best_candidate(onsets, [60.0, 120.0, 240.0])   # half / true / double
    assert best == 120.0


def test_scoring_picks_true_grid_when_only_beats_are_onsets():
    # true grid: 90 BPM (pulse 166.7 ms), onsets exactly on beats
    p = 60.0 / (4 * 90)
    onsets = [0.21 + k * p for k in range(48)]
    best, _ = _best_candidate(onsets, [45.0, 90.0, 180.0])
    assert best == 90.0


def test_scoring_weights_onset_strengths():
    onsets = np.array([0.0, 0.1])
    bpm, phase = 480.0, 0.0
    loud = score_candidate(onsets, bpm, phase, strengths=[1.0, 0.0])
    plain = score_candidate(onsets, bpm, phase)
    assert loud < plain  # the strong onset sits exactly on a pulse


# ---- (b) plausible-BPM prior --------------------------------------------------


def test_bpm_prior_penalty_range():
    assert bpm_prior_penalty(60.0) == 0.0
    assert bpm_prior_penalty(120.0) == 0.0
    assert bpm_prior_penalty(200.0) == 0.0
    assert bpm_prior_penalty(40.0) == PRIOR_PENALTY_S    # far below: capped
    assert bpm_prior_penalty(300.0) == PRIOR_PENALTY_S   # far above: capped
    assert 0.0 < bpm_prior_penalty(59.0) < PRIOR_PENALTY_S
    assert 0.0 < bpm_prior_penalty(210.0) < PRIOR_PENALTY_S


def test_prior_rejects_40_and_300_when_onsets_fit_equally():
    # onsets every 0.75 s: exactly on the pulses of both the 40 and the 80 BPM grid
    onsets = np.array([0.1 + 0.75 * k for k in range(8)])
    best, _ = _best_candidate(onsets, [40.0, 80.0])
    assert best == 80.0
    # onsets every 0.3 s: exactly on the pulses of both the 100 and the 300 BPM grid
    onsets = np.array([0.1 + 0.3 * k for k in range(10)])
    best, _ = _best_candidate(onsets, [100.0, 300.0])
    assert best == 100.0


# ---- (c) choose_grid_robust wiring --------------------------------------------


def _steady_beats(n=40, period=0.5, phase=0.4):
    return phase + period * np.arange(n)


def test_robust_off_behaves_like_legacy(monkeypatch):
    monkeypatch.setenv("ULTRAAI_BEAT_ROBUST", "0")
    beats = _steady_beats()
    first = 5.0
    legacy = choose_grid(120.0, beats, first)          # dispatch with the config off
    robust = choose_grid_robust(120.0, beats, first)    # ... and the robust entry point
    assert robust == legacy
    assert robust.bpm == 240.0                          # 0.5 s quarter -> k=2 -> 240
    assert robust.gap_s == pytest.approx(4.9)           # phase 0.4 + 9 quarters
    assert robust.tempo == pytest.approx(120.0)


def test_robust_on_uses_candidates_and_keeps_fallback(monkeypatch):
    monkeypatch.delenv("ULTRAAI_BEAT_ROBUST", raising=False)  # registered default "1"
    beats = _steady_beats()
    grid = choose_grid(120.0, beats, 5.0)
    assert grid.bpm == 240.0
    assert 0.0 <= grid.gap_s <= 5.0
    assert grid.tempo == pytest.approx(120.0)
    # beats only (no cached onsets): the robust path must not move the legacy grid
    monkeypatch.setenv("ULTRAAI_BEAT_ROBUST", "0")
    assert grid == choose_grid(120.0, beats, 5.0)
    monkeypatch.delenv("ULTRAAI_BEAT_ROBUST", raising=False)
    # few beats -> the old fallback path, robust or not
    few = _steady_beats(n=4)
    assert choose_grid(120.0, few, 5.0) == Grid(FALLBACK_BPM, 5.0, None)
    monkeypatch.setenv("ULTRAAI_BEAT_ROBUST", "0")
    assert choose_grid(120.0, few, 5.0) == Grid(FALLBACK_BPM, 5.0, None)


def test_robust_prefers_onset_grid_over_beat_grid(monkeypatch):
    monkeypatch.delenv("ULTRAAI_BEAT_ROBUST", raising=False)
    # beats claim 0.5 s quarters at phase 0.4, but the (stronger) onsets sit at 0.45 + 0.5k
    beats = _steady_beats(phase=0.4)
    onsets = 0.45 + 0.5 * np.arange(40)
    strengths = np.full(40, 10.0)
    grid = choose_grid_robust(120.0, beats, 5.0, onsets=onsets, strengths=strengths)
    p = 60.0 / (4 * grid.bpm)
    # the gap must sit on the onset grid (within one search step of the pulse)
    assert abs((grid.gap_s - 0.45) % p) < p / 100 or abs((grid.gap_s - 0.45) % p - p) < p / 100


# ---- (d) grid metrics in compare() --------------------------------------------

REF = """#TITLE:Ref
#ARTIST:Someone
#MP3:x.mp3
#BPM:120
#GAP:1000
: 8 4 60 la
: 12 4 62 la
-
E
"""


def _hyp(bpm, gap, notes=": 8 4 60 la\n: 12 4 62 la\n-\nE\n"):
    return f"#TITLE:Hyp\n#ARTIST:Someone\n#MP3:x.mp3\n#BPM:{bpm}\n#GAP:{gap}\n{notes}"


def test_grid_metrics_exact_values():
    ref = parse(REF)                       # first note: beat 8 -> t0 = 1.0 + 8*0.125 = 2.0 s
    hyp = parse(_hyp(240, 550))            # pulse 62.5 ms, gap 0.55 s
    m = compare(ref, hyp)
    assert m["grid_bpm_ratio"] == pytest.approx(240.0 / 120.0)
    # u = (2.0 - 0.55) / 0.0625 = 23.2 -> frac 0.2 -> 0.2 * 62.5 ms = 12.5 ms
    assert m["grid_phase_ms"] == pytest.approx(12.5)
    # identical grids -> ratio 1, phase 0
    m = compare(ref, parse(_hyp(120, 1000)))
    assert m["grid_bpm_ratio"] == pytest.approx(1.0)
    assert m["grid_phase_ms"] == pytest.approx(0.0)
    # signed: gap 1.05 s -> u = 7.6 -> frac -0.4 -> -50 ms
    m = compare(ref, parse(_hyp(120, 1050)))
    assert m["grid_phase_ms"] == pytest.approx(-50.0)


def test_grid_metrics_null_without_bpm_or_notes():
    from ultraai.eval.metrics import _grid_metrics

    ref = parse(REF)
    # hyp with no #BPM and no notes: compare() runs, grid metrics are null
    no_bpm = parse("#TITLE:Hyp\n#GAP:1000\nE\n")
    m = compare(ref, no_bpm)
    assert m["grid_bpm_ratio"] is None and m["grid_phase_ms"] is None
    # hyp with BPM but no notes -> null as well
    no_notes = parse(_hyp(240, 550, notes="E\n"))
    m = compare(ref, no_notes)
    assert m["grid_bpm_ratio"] is None and m["grid_phase_ms"] is None
    # a side that lacks #BPM (but has notes) also yields null in the grid metrics
    m = _grid_metrics(ref, parse(_hyp(0, 0).replace("#BPM:0\n", "")), 1)
    assert m["grid_bpm_ratio"] is None and m["grid_phase_ms"] is None
    # existing metrics are untouched and still present (hyp grid aligned to the ref notes)
    m = compare(ref, parse(_hyp(240, 1500)))
    assert m["word_recall"] == pytest.approx(1.0)
    assert "onset_within_250ms" in m and "pitch_class_acc" in m
    assert m["grid_bpm_ratio"] == pytest.approx(2.0) and m["grid_phase_ms"] == pytest.approx(0.0)
