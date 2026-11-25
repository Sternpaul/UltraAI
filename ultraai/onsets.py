"""Vocal onset detection (spectral flux) and snapping aligned words to those onsets.

Pure numpy — no librosa, so it is unit-testable in the light ``[test]`` env: the 16 kHz
mono vocal wave is framed into 1024-sample frames with hop 160 (10 ms), hann-windowed,
rfft magnitude taken, and the half-wave rectified frame-to-frame difference summed over
bins gives the onset envelope.  Peaks are local maxima above ``median + k * MAD``
(k = 1.0) with a minimum distance of 0.05 s.

``onsets_for`` caches the onset times next to the vocal stem as
``vocals.with_suffix('.onsets.npz')``, like the pitch cache in ``generate.pitch_track``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .align import AlignedWord

SR = 16000                 # vocal stems are mono 16 kHz (align.SR)
FRAME = 1024               # 64 ms analysis window
HOP = 160                  # 10 ms hop
PEAK_K = 1.0               # peak threshold = median + PEAK_K * MAD
MIN_DIST_S = 0.05          # min distance between picked peaks (seconds)


def onset_envelope(wave: np.ndarray, sr: int = SR, frame: int = FRAME, hop: int = HOP) -> np.ndarray:
    """Spectral-flux onset envelope of a mono wave: one value per frame pair.

    ``env[j]`` is the rectified magnitude increase between frame ``j`` and ``j + 1``,
    so it belongs to frame time ``(j + 1) * hop + frame / 2`` samples.
    """
    wave = np.asarray(wave, dtype=np.float64).ravel()
    if wave.size < frame + hop:
        return np.zeros(0)
    n_frames = 1 + (wave.size - frame) // hop
    win = np.hanning(frame)
    idx = np.arange(frame)[None, :] + hop * np.arange(n_frames)[:, None]
    mag = np.abs(np.fft.rfft(wave[idx] * win, axis=1))
    diff = np.diff(mag, axis=0)
    np.maximum(diff, 0.0, out=diff)
    return diff.sum(axis=1)


def pick_peaks(env: np.ndarray, sr: int = SR, frame: int = FRAME, hop: int = HOP,
               k: float = PEAK_K, min_dist_s: float = MIN_DIST_S) -> np.ndarray:
    """Onset times (seconds) of the envelope peaks: local maxima above median + k*MAD,
    greedily kept in descending height order with at least ``min_dist_s`` between them."""
    env = np.asarray(env, dtype=np.float64)
    if env.size < 3:
        return np.zeros(0)
    med = float(np.median(env))
    mad = float(np.median(np.abs(env - med)))
    scale = mad if mad > 0 else float(np.std(env))
    if scale <= 0:
        return np.zeros(0)
    thresh = med + k * scale
    inner = np.arange(1, env.size - 1)
    cand = inner[(env[inner] > env[inner - 1]) & (env[inner] >= env[inner + 1]) & (env[inner] > thresh)]
    times = (cand + 1) * hop + frame / 2.0  # env[j] belongs to frame j + 1 (frame center)
    times /= sr
    order = np.argsort(env[cand])[::-1]
    kept: list[float] = []
    for j in order:
        t = float(times[j])
        if all(abs(t - u) >= min_dist_s for u in kept):
            kept.append(t)
    return np.array(sorted(kept))


def detect_onsets(wave: np.ndarray, sr: int = SR) -> np.ndarray:
    """Onset times (seconds) of a mono wave."""
    return pick_peaks(onset_envelope(wave, sr=sr), sr=sr)


def onsets_for(vocals: Path, wave: np.ndarray, sr: int = SR) -> np.ndarray:
    """Onset times of the vocals, cached next to them as ``vocals.with_suffix('.onsets.npz')``."""
    cache = vocals.with_suffix(".onsets.npz")
    if cache.exists():
        return np.load(cache)["times"]
    times = detect_onsets(wave, sr=sr)
    np.savez(cache, times=times)
    return times


def snap_words_to_onsets(words: list[AlignedWord], onsets: np.ndarray, win: float) -> tuple[list, dict]:
    """Snap each word's onset to the nearest detected vocal onset within ``win`` seconds.

    A word is moved by ``delta = onset - start`` only if ``|delta| <= win``; the move is
    clamped so the new start stays >= the previous word's (snapped) end - 0.02 and never
    later than the next word's start - 0.05, so a word never crosses its neighbours.
    ``start``, ``end`` and every ``syllable_start`` shift by ``delta`` (syllable starts are
    kept non-decreasing within the word).  Returns ``(words, stats)`` with stats =
    ``{"count": words moved, "max_abs_delta": largest |delta| applied}``.
    """
    onsets = np.asarray(onsets, dtype=np.float64).ravel()
    stats = {"count": 0, "max_abs_delta": 0.0}
    prev_end: float | None = None
    for i, w in enumerate(words):
        delta = 0.0
        if onsets.size:
            t = float(onsets[int(np.argmin(np.abs(onsets - w.start)))])
            if abs(t - w.start) <= win:
                delta = t - w.start
        if prev_end is not None:
            lo = prev_end - 0.02
            if w.start + delta < lo:
                delta = lo - w.start
        if i + 1 < len(words):
            hi = words[i + 1].start - 0.05
            if w.start + delta > hi:
                delta = hi - w.start
        if delta != 0.0:
            w.start += delta
            w.end += delta
            w.syllable_starts = [s + delta for s in w.syllable_starts]
            for k in range(1, len(w.syllable_starts)):  # keep non-decreasing within the word
                if w.syllable_starts[k] < w.syllable_starts[k - 1]:
                    w.syllable_starts[k] = w.syllable_starts[k - 1]
            stats["count"] += 1
            stats["max_abs_delta"] = max(stats["max_abs_delta"], abs(delta))
        prev_end = w.end
    return words, stats
