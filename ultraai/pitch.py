"""Vocal pitch tracking -> per-frame MIDI pitch with voicing confidence.

Models (key: name, speed for a 4-minute song on a 4-core CPU):
  swift       SwiftF0                                    seconds   (default)
  pesto       PESTO, trained on sung vocals              ~1 min
  crepe-tiny  CREPE tiny, the classic neural pitch model ~1-2 min
  crepe-full  CREPE full, most accurate CREPE            ~20 min on CPU, fast on a GPU
All torch models use a CUDA GPU automatically when one is available.
"""

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


PITCH_MODELS = {"swift": "SwiftF0", "pesto": "PESTO", "crepe-tiny": "CREPE tiny", "crepe-full": "CREPE full"}


def device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _loudness(wave16k: np.ndarray, times: np.ndarray, win: int = 1024) -> np.ndarray:
    out = np.empty(len(times))
    for i, t in enumerate(times):
        c = int(t * 16000)
        seg = wave16k[max(0, c - win // 2): c + win // 2]
        out[i] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) if len(seg) else -120.0
    return out


def track(wave16k: np.ndarray, model: str = "swift") -> PitchTrack:
    if model in ("crepe", "crepe-tiny", "crepe-full"):
        return _track_crepe(wave16k, "full" if model == "crepe-full" else CREPE_SIZE)
    if model == "pesto":
        return _track_pesto(wave16k)
    from swift_f0 import SwiftF0

    res = SwiftF0().detect(wave16k, 16000, fmin=FMIN, fmax=FMAX)
    hz = np.asarray(res.pitch_hz, dtype=float)
    conf = np.asarray(res.confidence, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(hz / 440.0)
    midi[(hz <= 0) | ~np.isfinite(midi)] = np.nan
    return PitchTrack(np.asarray(res.timestamps, dtype=float), midi, conf,
                      np.asarray(res.loudness_db, dtype=float))


HOP_S = 0.01
CREPE_SIZE = __import__("os").environ.get("ULTRAAI_CREPE", "tiny")  # "full" is ~10x slower on CPU


def _to_track(wave16k, times, hz, conf) -> PitchTrack:
    hz = np.asarray(hz, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(hz / 440.0)
    bad = (hz <= 0) | ~np.isfinite(midi) | (hz < FMIN) | (hz > FMAX)
    midi[bad] = np.nan
    conf = np.asarray(conf, dtype=float)
    conf[bad] = 0.0
    times = np.asarray(times, dtype=float)
    return PitchTrack(times, midi, conf, _loudness(wave16k, times))


def _track_crepe(wave16k: np.ndarray, size: str = "tiny") -> PitchTrack:
    import torch
    import torchcrepe

    x = torch.from_numpy(np.asarray(wave16k, dtype=np.float32))[None]
    hop = int(16000 * HOP_S)
    with torch.inference_mode():
        f0, per = torchcrepe.predict(x, 16000, hop, FMIN, FMAX, model=size, return_periodicity=True,
                                     batch_size=512, device=device())
    f0, per = f0.cpu(), per.cpu()
    per = torchcrepe.filter.median(per, 3)
    f0, per = f0[0].numpy(), per[0].numpy()
    times = np.arange(len(f0)) * HOP_S
    # periodicity 0..1 -> confidence: CREPE's own voicing threshold is ~0.2-0.3
    return _to_track(wave16k, times, f0, np.clip(per / 0.4, 0, 1))


def _track_pesto(wave16k: np.ndarray) -> PitchTrack:
    import torch
    import pesto

    # in 30 s pieces: the whole song at once needs >10 GB of memory
    chunk = 30 * 16000
    ts, fs, cs = [], [], []
    wave = np.asarray(wave16k, dtype=np.float32)
    for k in range(0, len(wave), chunk):
        x = torch.from_numpy(wave[k:k + chunk]).to(device())
        if len(x) < 2048:
            break
        with torch.inference_mode():
            t, f0, conf, _ = pesto.predict(x, 16000, step_size=HOP_S * 1000)
        t = t.cpu().numpy()
        t = t / (1000.0 if float(t.max()) > 100 else 1.0)
        ts.append(t + k / 16000)
        fs.append(f0.cpu().numpy())
        cs.append(conf.cpu().numpy())
    if not ts:
        return _to_track(wave16k, np.zeros(1), np.zeros(1), np.zeros(1))
    return _to_track(wave16k, np.concatenate(ts), np.concatenate(fs), np.concatenate(cs))
