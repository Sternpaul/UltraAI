"""Tempo grid: pick #BPM/#GAP so beats line up with the music (like hand-made files).

``choose_grid`` has two paths: the legacy steady-grid fit, and (``beat_robust=1``, the
default) a robust choice that scores candidate (bpm, phase) grids — including half and
double tempo — against the detected onsets, with a plausible-song-BPM prior so
beat-tracker octave errors lose.  The scoring helpers are pure numpy and need no librosa.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

TARGET_BEAT_S = (0.037, 0.075)   # library: 39..64 ms (p10..p90)
FALLBACK_BPM = 300.0             # 50 ms beats when the tempo is not steady

# Plausible song tempi (quarter notes / min): a candidate outside this range is a
# beat-tracker octave error and is penalised even when the onsets fit it equally well.
PLAUSIBLE_BPM = (60.0, 200.0)
PRIOR_PENALTY_S = 0.25           # max seconds added to a candidate outside PLAUSIBLE_BPM
EMPTY_PULSE_PENALTY_S = 0.05     # max seconds added for grid pulses with no nearby onset
PHASE_SEARCH_STEPS = 180         # resolution of the per-candidate phase search
TIEBREAK_W = 1e-6                # seconds; on exact score ties prefer the detected tempo
CAND_MARGIN_S = 0.002            # mean-onset-fit improvement required to override the legacy grid


@dataclass
class Grid:
    bpm: float          # UltraStar #BPM (one beat = 60 / (4 * bpm) s)
    gap_s: float        # time of beat 0
    tempo: float | None  # detected musical tempo (quarter notes / min), None if unsteady

    @property
    def beat_s(self) -> float:
        return 60.0 / (4.0 * self.bpm)


def detect_beats(mix: Path) -> tuple[float, np.ndarray]:
    """(tempo, beat times) of the full mix, cached next to it.

    The cache also stores onset times and strengths (extra keys; readers that do not
    know them keep working, and old caches without them simply mean "no onsets").
    """
    cache = mix.with_suffix(".beats.npz")
    if cache.exists():
        z = np.load(cache)
        return float(z["tempo"]), z["beats"]
    import librosa

    y, sr = librosa.load(mix, sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    oframes = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
    onsets = librosa.frames_to_time(oframes, sr=sr)
    env = librosa.onset.onset_strength(y=y, sr=sr)
    strengths = np.asarray(env[oframes], dtype=float) if len(oframes) else np.zeros(0)
    np.savez(cache, tempo=tempo, beats=beats, onsets=onsets, strengths=strengths)
    return tempo, beats


def load_onsets(mix: Path) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(onset times, onset strengths) from the beats cache; (None, None) for old caches."""
    cache = mix.with_suffix(".beats.npz")
    if not cache.exists():
        return None, None
    z = np.load(cache)
    on = z["onsets"] if "onsets" in z.files else None
    st = z["strengths"] if "strengths" in z.files else None
    if on is not None and (st is None or len(st) != len(on)):
        st = None
    return on, st


def _steady_grid(tempo: float, beats: np.ndarray, first_note: float, max_cv: float = 0.04) -> Grid:
    """Legacy choice: tempo-locked grid if the beat is steady, else a fixed 50 ms grid
    starting at the first note."""
    if len(beats) >= 16:
        ibi = np.diff(beats)
        med = float(np.median(ibi))
        steady = np.mean(np.abs(ibi - med) < 0.08 * med)  # share of beats on tempo (tracker jitter ~23 ms)
        # refine the tempo with a least-squares fit of beat index vs time
        idx = np.round((beats - beats[0]) / med)
        slope = float(np.polyfit(idx, beats, 1)[0])
        if steady >= 0.8 and slope > 0:
            q = slope                       # seconds per quarter note
            tempo = 60.0 / q
            k = 1.0                         # bpm = tempo * k  -> beat = q / (4k)
            while q / (4 * k) > TARGET_BEAT_S[1]:
                k *= 2
            while q / (4 * k) < TARGET_BEAT_S[0]:
                k /= 2
            # phase: a quarter-note boundary from the fitted line, at or before the first note
            phase = float(np.median(beats - idx * q))
            m = np.floor((first_note - phase) / q)
            gap = phase + m * q
            if gap < 0:
                gap += q * np.ceil(-gap / q)
                if gap > first_note:      # first note very early: fall back below
                    gap = None
            if gap is not None:
                return Grid(round(tempo * k, 3), gap, tempo)
    return Grid(FALLBACK_BPM, max(0.0, first_note), None)


# ---- robust candidate scoring (pure numpy, no librosa) -------------------------


def pulse_s(bpm: float) -> float:
    """Seconds between two pulses of a #BPM grid."""
    return 60.0 / (4.0 * bpm)


def onset_grid_distances(onsets, bpm: float, phase_s: float) -> np.ndarray:
    """Absolute distance (s) of each onset to the nearest pulse of the grid."""
    p = pulse_s(bpm)
    d = (np.asarray(onsets, dtype=float) - phase_s) / p
    return np.abs(d - np.round(d)) * p


def bpm_prior_penalty(bpm: float, plausible: tuple[float, float] = PLAUSIBLE_BPM,
                      max_penalty_s: float = PRIOR_PENALTY_S) -> float:
    """Penalty (s) for a candidate tempo outside the plausible song-BPM range.

    Grows with the relative distance outside the range and is capped at
    ``max_penalty_s``, so a 40 or 300 BPM octave error always loses to an in-range
    candidate whose onset fit is no better.
    """
    lo, hi = plausible
    if lo <= bpm <= hi:
        return 0.0
    rel = (lo - bpm) / lo if bpm < lo else (bpm - hi) / hi
    return max_penalty_s * min(1.0, max(0.0, rel) * 5.0)


def empty_pulse_share(onsets, bpm: float, phase_s: float, t0: float, t1: float,
                      tol_s: float = 0.06) -> float:
    """Share of grid pulses in [t0, t1] with no onset within ``tol_s``.

    Guards against over-dense (double-tempo) grids: they put every onset on a pulse but
    leave most pulses empty.
    """
    p = pulse_s(bpm)
    if t1 <= t0 or p <= 0:
        return 0.0
    on = np.asarray(onsets, dtype=float)
    n0 = int(np.floor((t0 - phase_s) / p))
    n1 = int(np.ceil((t1 - phase_s) / p))
    pulses = phase_s + np.arange(n0, n1 + 1) * p
    pulses = pulses[(pulses >= t0) & (pulses <= t1)]
    if not len(pulses) or not len(on):
        return 0.0
    d = np.abs(pulses[:, None] - on[None, :]).min(axis=1)
    return float(np.mean(d > tol_s))


def score_candidate(onsets, bpm: float, phase_s: float, strengths=None,
                    t0: float | None = None, t1: float | None = None,
                    prior_bpm: float | None = None) -> float:
    """Score one candidate grid (lower = better), in seconds.

    strength-weighted mean |onset -> nearest pulse| distance
    + EMPTY_PULSE_PENALTY_S * empty_pulse_share   (when a span t0..t1 is given)
    + bpm_prior_penalty(prior_bpm or bpm)         (implausible song tempi lose)
    """
    on = np.asarray(onsets, dtype=float)
    d = onset_grid_distances(on, bpm, phase_s)
    if strengths is not None:
        w = np.asarray(strengths, dtype=float)
        if len(w) == len(d) and float(w.sum()) > 0:
            base = float(np.sum(w * d) / np.sum(w))
        else:
            base = float(np.mean(d))
    else:
        base = float(np.mean(d))
    score = base + bpm_prior_penalty(prior_bpm if prior_bpm is not None else bpm)
    if t0 is not None and t1 is not None:
        score += EMPTY_PULSE_PENALTY_S * empty_pulse_share(on, bpm, phase_s, t0, t1)
    return score


def best_phase(onsets, bpm: float, strengths=None, steps: int = PHASE_SEARCH_STEPS) -> float:
    """Phase in [0, pulse) that minimises the (strength-weighted) onset distance."""
    p = pulse_s(bpm)
    on = np.asarray(onsets, dtype=float)
    r = np.mod(on, p)
    grid = np.linspace(0.0, p, steps, endpoint=False)
    d = np.abs(r[:, None] - grid[None, :])
    d = np.minimum(d, p - d)
    if strengths is not None:
        w = np.asarray(strengths, dtype=float)
        if len(w) == len(on) and float(w.sum()) > 0:
            s = (w[:, None] * d).sum(axis=0) / float(w.sum())
        else:
            s = d.mean(axis=0)
    else:
        s = d.mean(axis=0)
    return float(grid[int(np.argmin(s))])


def _fit_period(beats: np.ndarray) -> tuple[float, float]:
    """Least-squares (period_s, phase_s) of the beat track: beat k at phase + k*period."""
    b = np.asarray(beats, dtype=float)
    med = float(np.median(np.diff(b)))
    idx = np.round((b - b[0]) / med)
    slope = float(np.polyfit(idx, b, 1)[0])
    if slope <= 0:
        slope = med
    return slope, float(np.median(b - idx * slope))


def choose_grid(tempo: float, beats: np.ndarray, first_note: float, max_cv: float = 0.04,
                onsets=None, strengths=None) -> Grid:
    """Pick the #BPM/#GAP grid.  With ``beat_robust=1`` (default) the half/double-tempo
    candidates around the detected tempo are scored against the onsets; with ``0`` the
    legacy steady-grid choice runs unchanged."""
    if config.get_bool("beat_robust"):
        return choose_grid_robust(tempo, beats, first_note, onsets=onsets,
                                  strengths=strengths, max_cv=max_cv)
    return _steady_grid(tempo, beats, first_note, max_cv)


def choose_grid_robust(tempo: float, beats: np.ndarray, first_note: float, max_cv: float = 0.04,
                       onsets=None, strengths=None) -> Grid:
    """Robust grid choice: when beats exist and are steady, score the legacy grid plus the
    candidate grids at tempo x {1/2, 1, 2} (each mapped into TARGET_BEAT_S like the legacy
    fit) against the onsets — or the beats when no onsets are cached — and take the best;
    the plausible-BPM prior settles octave ties.  A candidate only replaces the legacy
    grid when its onset fit is clearly better (CAND_MARGIN_S), so a correct beat track
    yields exactly the legacy #BPM/#GAP.  Without (steady) beats this is exactly the
    legacy path, including the fixed fallback grid."""
    if not config.get_bool("beat_robust") or len(beats) < 16:
        return _steady_grid(tempo, beats, first_note, max_cv)
    b = np.asarray(beats, dtype=float)
    ibi = np.diff(b)
    med = float(np.median(ibi))
    steady = float(np.mean(np.abs(ibi - med) < 0.08 * med))
    legacy = _steady_grid(tempo, beats, first_note, max_cv)
    if steady < 0.8 or legacy.tempo is None:   # unsteady or no tempo: legacy (fallback) path
        return legacy
    q, _phase0 = _fit_period(b)
    src = np.asarray(onsets, dtype=float) if onsets is not None and len(onsets) else b
    w = (np.asarray(strengths, dtype=float)
         if strengths is not None and len(strengths) == len(src) else None)
    t0, t1 = float(src[0]), float(src[-1])
    ref_t = tempo if tempo and tempo > 0 else 60.0 / q
    legacy_sc = score_candidate(src, legacy.bpm, legacy.gap_s, w, t0=t0, t1=t1,
                                prior_bpm=legacy.tempo)
    best = (legacy_sc, legacy.bpm, legacy.gap_s, legacy.tempo)
    for mult in (0.5, 1.0, 2.0):
        t_cand = 60.0 / q * mult            # candidate quarter-note tempo
        k = 1.0
        while (60.0 / t_cand) / (4 * k) > TARGET_BEAT_S[1]:
            k *= 2
        while (60.0 / t_cand) / (4 * k) < TARGET_BEAT_S[0]:
            k /= 2
        bpm = t_cand * k
        phase = best_phase(src, bpm, w)
        sc = score_candidate(src, bpm, phase, w, t0=t0, t1=t1, prior_bpm=t_cand)
        sc += TIEBREAK_W * abs(np.log(t_cand / ref_t))  # octaves collapse to one grid: keep the detected tempo
        if sc < best[0]:
            best = (sc, bpm, phase, t_cand)
    sc, bpm, phase, t_cand = best
    # only override the legacy grid when the winner is clearly better
    if sc > legacy_sc - CAND_MARGIN_S:
        return legacy
    p = pulse_s(bpm)
    m = np.floor((first_note - phase) / p)
    gap = float(phase + m * p)
    if gap < 0:
        gap += p * np.ceil(-gap / p)
        if gap > first_note:              # first note very early: anchor at it
            gap = max(0.0, first_note)
    return Grid(round(bpm, 3), gap, t_cand)
