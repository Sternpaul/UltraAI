"""Vocal pitch tracking -> per-frame MIDI pitch with voicing confidence.

Models (key: name, speed for a 4-minute song on a CPU):
  swift       SwiftF0                                    seconds   (default)
  pesto       PESTO, trained on sung vocals              ~1 min
  crepe-tiny  CREPE tiny, the classic neural pitch model ~1-2 min
  crepe-full  CREPE full, most accurate CREPE            ~20 min on CPU, fast on a GPU
  rmvpe       RMVPE (RVC), made for singing in music     ~1-2 min; 362 MB model download
  fcpe        FCPE (torchfcpe), fast singing model       ~30 s
  vote        median of SwiftF0 + RMVPE + FCPE per frame (an ensemble)
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


PITCH_MODELS = {"swift": "SwiftF0", "pesto": "PESTO", "crepe-tiny": "CREPE tiny", "crepe-full": "CREPE full",
                "rmvpe": "RMVPE", "fcpe": "FCPE", "vote": "Vote (SwiftF0 + RMVPE + FCPE)"}


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
    if model == "rmvpe":
        return _track_rmvpe(wave16k)
    if model == "fcpe":
        return _track_fcpe(wave16k)
    if model == "vote":
        return vote([track(wave16k, m) for m in ("swift", "rmvpe", "fcpe")], wave16k)
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


RMVPE_URL = "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.onnx"
_rmvpe = None


def _rmvpe_session():
    global _rmvpe
    if _rmvpe is None:
        import urllib.request
        from pathlib import Path

        import onnxruntime as ort

        from .audio import CACHE_DIR

        f = CACHE_DIR / "models" / "rmvpe.onnx"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(RMVPE_URL, f.with_suffix(".part"))
            f.with_suffix(".part").rename(f)
        prov = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device() == "cuda" else ["CPUExecutionProvider"]
        _rmvpe = ort.InferenceSession(str(Path(f)), providers=prov)
    return _rmvpe


def _track_rmvpe(wave16k: np.ndarray, threshold: float = 0.03) -> PitchTrack:
    """RMVPE (Wei et al. 2023) via the RVC project's ONNX export: 128-band log-mel input,
    360-bin pitch salience (20 cents per bin) output, decoded around the argmax."""
    import librosa

    wave = np.asarray(wave16k, dtype=np.float32)
    mag = np.abs(librosa.stft(wave, n_fft=1024, hop_length=160, win_length=1024, window="hann", center=True))
    fb = librosa.filters.mel(sr=16000, n_fft=1024, n_mels=128, fmin=30, fmax=8000, htk=True)
    mel = np.log(np.clip(fb @ mag, 1e-5, None)).astype(np.float32)
    n = mel.shape[1]
    sess = _rmvpe_session()
    sal = []
    step = 32 * 512  # chunks of ~2.6 min keep memory low
    for k in range(0, n, step):
        m = mel[:, k:k + step]
        pad = (-m.shape[1]) % 32
        m = np.pad(m, ((0, 0), (0, pad)), mode="constant")
        out = sess.run(None, {sess.get_inputs()[0].name: m[None]})[0][0]
        sal.append(out[: m.shape[1] - pad])
    sal = np.concatenate(sal)[:n]
    cents_map = 20 * np.arange(360) + 1997.3794084376191
    center = sal.argmax(1)
    f0 = np.zeros(n)
    conf = sal.max(1)
    for i in range(n):
        lo, hi = max(0, center[i] - 4), min(360, center[i] + 5)
        w = sal[i, lo:hi]
        c = float((w * cents_map[lo:hi]).sum() / max(w.sum(), 1e-9))
        f0[i] = 10 * 2 ** (c / 1200) if conf[i] > threshold else 0.0
    times = np.arange(n) * 0.01
    return _to_track(wave16k, times, f0, np.clip(conf / 0.5, 0, 1) * (f0 > 0))


def _track_fcpe(wave16k: np.ndarray) -> PitchTrack:
    import torch
    from torchfcpe import spawn_bundled_infer_model

    model = spawn_bundled_infer_model(device=device())
    x = torch.from_numpy(np.asarray(wave16k, dtype=np.float32))[None, :, None].to(device())
    n = len(wave16k) // 160 + 1
    with torch.inference_mode():
        f0 = model.infer(x, sr=16000, decoder_mode="local_argmax", threshold=0.006,
                         f0_min=FMIN, f0_max=FMAX, interp_uv=False, output_interp_target_length=n)
    f0 = f0.squeeze().cpu().numpy()
    times = np.arange(len(f0)) * 0.01
    return _to_track(wave16k, times, f0, (f0 > 0).astype(float))


def vote(tracks: list[PitchTrack], wave16k) -> PitchTrack:
    """Per 10 ms frame: the median pitch of the models that hear a voice (octave-folded to
    their median), voiced if most of them do.  Ensembles cut octave errors a lot."""
    grid = np.arange(0, max(t.times[-1] for t in tracks if len(t.times)), 0.01)
    ms, vs = [], []
    for t in tracks:
        idx = np.clip(np.searchsorted(t.times, grid), 0, len(t.times) - 1)
        v = t.voiced()[idx]
        m = np.where(v, t.midi[idx], np.nan)
        ms.append(m)
        vs.append(v)
    M = np.vstack(ms)
    with np.errstate(all="ignore"):
        ref = np.nanmedian(M, axis=0)
    M = M - 12 * np.round((M - ref) / 12)
    with np.errstate(all="ignore"):
        med = np.nanmedian(M, axis=0)
    voiced = np.vstack(vs).sum(0) >= (len(tracks) // 2 + 1)
    midi = np.where(voiced, med, np.nan)
    conf = np.vstack(vs).mean(0)
    return PitchTrack(grid, midi, np.where(voiced, np.maximum(conf, 0.5), 0.0), _loudness(wave16k, grid))
