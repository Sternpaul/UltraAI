"""Optional key detection, key snap and octave range for the final note pitches (B5b).

Everything here is off by default (``ULTRAAI_KEYSNAP=0``, ``ULTRAAI_OCTAVE_RANGE=''``),
so the generated .txt is byte-identical unless a feature is switched on.  The wiring
lives in generate.py: :func:`fit_syllables` runs after the final note pitches are set
(after the double-check auto-fix) and before ``to_song``; timing is never touched.

Pure numpy + the option registry, so it is unit-testable without the worker install.
"""

from __future__ import annotations

import numpy as np

from . import config

# Krumhansl-Kessler key profiles (tonic first): perceived stability of the 12 scale
# degrees in a major / minor key.  Used rotated against the pitch-class histogram.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)          # pitch classes relative to the tonic
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)          # natural minor


def detect_key(p) -> tuple[int, bool]:
    """Estimate ``(tonic pitch class, is_minor)`` of a :class:`~ultraai.pitch.PitchTrack`.

    The voiced frames build a pitch-class histogram weighted by duration x confidence
    (every frame lasts ``p.frame`` seconds); each of the 24 rotated Krumhansl-Schmuckler
    major/minor profiles is scored against it by Pearson correlation and the best wins.
    Returns ``(0, False)`` when nothing is voiced.
    """
    voiced = p.voiced()
    midi = np.asarray(p.midi, dtype=float)[voiced]
    if midi.size == 0:
        return 0, False
    w = np.asarray(p.confidence, dtype=float)[voiced] * p.frame
    pcs = np.mod(np.rint(midi).astype(int), 12)
    hist = np.zeros(12)
    np.add.at(hist, pcs, w)
    if hist.sum() <= 0:  # degenerate confidence: fall back to plain frame counts
        np.add.at(hist, pcs, 1.0)
    hist = hist - hist.mean()
    best_r, best = -2.0, (0, False)
    for tonic in range(12):
        for profile, minor in ((MAJOR_PROFILE, False), (MINOR_PROFILE, True)):
            rot = np.roll(profile, tonic)
            rot = rot - rot.mean()
            denom = float(np.sqrt((rot * rot).sum() * (hist * hist).sum()))
            r = float((rot * hist).sum() / denom) if denom > 0 else -2.0
            if r > best_r:
                best_r, best = r, (tonic, minor)
    return best


def snap_to_key(pitches, tonic_pc: int, minor: bool):
    """Move each midi pitch by the minimal ``|delta| <= 6`` semitones into the scale.

    In-scale pitches are returned unchanged.  On a tie (equidistant scale tone above
    and below) the pitch rounds down.  Accepts a scalar or an array; same shape back.
    """
    scale = MINOR_SCALE if minor else MAJOR_SCALE
    arr = np.asarray(pitches)
    scalar = arr.ndim == 0
    p = np.atleast_1d(arr).astype(np.int64)
    rel = np.mod(p - int(tonic_pc), 12)
    best_abs = np.full(p.shape, 1 << 30, dtype=np.int64)
    best_d = np.zeros(p.shape, dtype=np.int64)
    for s in scale:
        d = np.mod(s - rel, 12)               # 0..11
        d = np.where(d > 6, d - 12, d)        # -5..6, |d| <= 6
        ad = np.abs(d)
        take = (ad < best_abs) | ((ad == best_abs) & (d < best_d))
        best_abs = np.where(take, ad, best_abs)
        best_d = np.where(take, d, best_d)
    out = p + best_d
    return int(out[0]) if scalar else out


def fold_octave(pitches, lo: int, hi: int):
    """Fold each midi pitch by whole octaves into ``[lo, hi]`` (pitch class preserved).

    Out-of-range pitches move by the minimal number of whole octaves into the range
    (so a pitch already inside stays put); when the range is narrower than an octave
    (``hi - lo < 11``) the folded value is clamped.  Scalar in -> scalar out.
    """
    arr = np.asarray(pitches)
    scalar = arr.ndim == 0
    p = np.atleast_1d(arr).astype(np.int64)
    out = p.copy()
    above = out > hi
    below = out < lo
    out[above] = out[above] - 12 * np.ceil((out[above] - hi) / 12.0)
    out[below] = out[below] + 12 * np.ceil((lo - out[below]) / 12.0)
    out = np.clip(out, lo, hi)
    return int(out[0]) if scalar else out


def parse_octave_range(text: str) -> tuple[int, int] | None:
    """Parse ``ULTRAAI_OCTAVE_RANGE`` ('48,72'; ':' also accepted) -> ``(lo, hi)`` midi.

    Empty/off -> ``None``.  Raises ValueError on anything that is not two integers.
    """
    text = (text or "").strip().replace(":", ",")
    if not text:
        return None
    parts = [x.strip() for x in text.split(",") if x.strip()]
    if len(parts) != 2:
        raise ValueError(f"octave_range must be 'lo,hi' in midi (e.g. 48,72), got {text!r}")
    lo, hi = int(parts[0]), int(parts[1])
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def fit_syllables(syl: list, p) -> int:
    """Apply keysnap + octave_range to the final pitches of every syllable, in place.

    ``syl``: the Syllable list of generate.py; ``p``: its PitchTrack (key detection).
    Melisma segment pitches shift together with their syllable; timing is untouched.
    Returns the number of syllables whose pitch changed.  With both options off (the
    defaults) nothing is touched and 0 is returned immediately.
    """
    keysnap = config.get_bool("keysnap")
    rng = parse_octave_range(config.get("octave_range"))
    if not keysnap and rng is None:
        return 0
    tonic, minor = detect_key(p) if keysnap else (0, False)
    changed = 0
    for s in syl:
        pitches = [s.pitch] + [m for _, _, m in s.segments] if s.segments else [s.pitch]
        new = list(pitches)
        if keysnap:
            new = [int(x) for x in np.atleast_1d(snap_to_key(np.array(new), tonic, minor))]
        if rng is not None:
            new = [int(x) for x in np.atleast_1d(fold_octave(np.array(new), *rng))]
        if new != pitches:
            changed += 1
            s.pitch = new[0]
            if s.segments:
                s.segments = [(t0, t1, m) for (t0, t1, _), m in zip(s.segments, new[1:])]
    return changed
