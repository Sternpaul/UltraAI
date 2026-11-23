"""Tempo grid: pick #BPM/#GAP so beats line up with the music (like hand-made files)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

TARGET_BEAT_S = (0.037, 0.075)   # library: 39..64 ms (p10..p90)
FALLBACK_BPM = 300.0             # 50 ms beats when the tempo is not steady


@dataclass
class Grid:
    bpm: float          # UltraStar #BPM (one beat = 60 / (4 * bpm) s)
    gap_s: float        # time of beat 0
    tempo: float | None  # detected musical tempo (quarter notes / min), None if unsteady

    @property
    def beat_s(self) -> float:
        return 60.0 / (4.0 * self.bpm)


def detect_beats(mix: Path) -> tuple[float, np.ndarray]:
    """(tempo, beat times) of the full mix, cached next to it."""
    cache = mix.with_suffix(".beats.npz")
    if cache.exists():
        z = np.load(cache)
        return float(z["tempo"]), z["beats"]
    import librosa

    y, sr = librosa.load(mix, sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(frames, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    np.savez(cache, tempo=tempo, beats=beats)
    return tempo, beats


def choose_grid(tempo: float, beats: np.ndarray, first_note: float, max_cv: float = 0.04) -> Grid:
    """Tempo-locked grid if the beat is steady, else a fixed 50 ms grid starting at the first note."""
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
