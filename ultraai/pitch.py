"""Vocal pitch tracking (SwiftF0) -> per-frame MIDI pitch with voicing confidence."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FMIN, FMAX = 65.0, 1100.0   # C2 .. C#6: covers sung vocals incl. falsetto


@dataclass
class PitchTrack:
    times: np.ndarray        # seconds, frame centres
    midi: np.ndarray         # float MIDI pitch (nan where unvoiced)
    confidence: np.ndarray   # 0..1, voiced if >= 0.5
    loudness_db: np.ndarray

    @property
    def frame(self) -> float:
        return float(self.times[1] - self.times[0]) if len(self.times) > 1 else 0.016

    def voiced(self, threshold: float = 0.5) -> np.ndarray:
        return (self.confidence >= threshold) & ~np.isnan(self.midi)

    def index(self, t: float) -> int:
        return int(np.clip(np.searchsorted(self.times, t), 0, len(self.times) - 1))


def track(wave16k: np.ndarray) -> PitchTrack:
    from swift_f0 import SwiftF0

    res = SwiftF0().detect(wave16k, 16000, fmin=FMIN, fmax=FMAX)
    hz = np.asarray(res.pitch_hz, dtype=float)
    conf = np.asarray(res.confidence, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(hz / 440.0)
    midi[(hz <= 0) | ~np.isfinite(midi)] = np.nan
    return PitchTrack(np.asarray(res.timestamps, dtype=float), midi, conf,
                      np.asarray(res.loudness_db, dtype=float))
