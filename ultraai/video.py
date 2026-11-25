"""#VIDEOGAP from onset-envelope cross-correlation (B5b).

When a song ships with a video file next to the audio, the video's own audio track
(decoded with ffmpeg to 16 kHz mono, cached in the audio's work dir) is compared
against the song audio: both get a spectral-flux onset envelope (the onsets.py
machinery, 10 ms frames) and the integer frame lag with the best normalized
cross-correlation within +-10 s is the gap.

Convention (documented, pinned by tests/test_songfit.py):

``best_lag(env_a, env_b, sr)`` returns ``L`` seconds with ``env_b(t) ~ env_a(t - L)``,
i.e. ``audio_time = video_time - L``.  ``L > 0`` means the video's audio starts
``L`` seconds LATER than the song audio (the video file has a lead-in).  ``#VIDEOGAP``
is written as ``L`` with 2 decimals, so a positive #VIDEOGAP means the video starts
that many seconds AFTER the audio (USDX convention).  Unit: seconds.

ffmpeg stays inside :func:`video_gap`; ``find_video`` / ``best_lag`` / the envelopes
are pure and unit-tested without it.  Default on (``ULTRAAI_VIDEOGAP=1``) but a no-op
when no sibling video exists, so audio-only songs keep a byte-identical .txt.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from . import config
from .onsets import HOP, SR, onset_envelope  # noqa: F401  (re-exported: same machinery)

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi")
MAX_LAG_S = 10.0


def find_video(audio_path) -> Path | None:
    """Sibling video with the same stem as the audio (.mp4/.mkv/.webm/.mov/.avi)."""
    audio_path = Path(audio_path)
    for cand in sorted(audio_path.parent.glob(audio_path.stem + ".*")):
        if cand.is_file() and cand.suffix.lower() in VIDEO_EXTS:
            return cand
    return None


def best_lag(env_a, env_b, sr: int = SR, hop: int = HOP, max_lag_s: float = MAX_LAG_S) -> float:
    """Lag in seconds of the best integer-lag normalized cross-correlation.

    Returns ``L`` with ``env_b(t) ~ env_a(t - L)``: positive ``L`` = env_b (the video)
    starts later than env_a (the audio).  Lags are searched in ``+-max_lag_s``.
    """
    a = np.asarray(env_a, dtype=np.float64).ravel()
    b = np.asarray(env_b, dtype=np.float64).ravel()
    n = min(a.size, b.size)
    if n < 3:
        return 0.0
    a, b = a[:n], b[:n]
    a0, b0 = a - a.mean(), b - b.mean()
    max_k = min(int(round(max_lag_s * sr / hop)), n - 1)
    min_overlap = max(32, n // 8)  # tiny overlaps correlate spuriously: ignore those lags
    best_r, best_k = -2.0, 0
    for k in range(-max_k, max_k + 1):
        if n - abs(k) < min_overlap:
            continue
        if k >= 0:  # b[j] matched with a[j - k]: b delayed by k frames
            x, y = a0[: n - k], b0[k:]
        else:       # b[j] matched with a[j - k], k < 0: b advanced
            x, y = a0[-k:], b0[: n + k]
        denom = float(np.sqrt((x * x).sum() * (y * y).sum()))
        r = float((x * y).sum() / denom) if denom > 0 else -2.0
        if r > best_r:
            best_r, best_k = r, k
    return best_k * hop / sr


def _decode_mono16k(src: Path, out: Path) -> bool:
    """Decode the audio track of ``src`` with ffmpeg to 16 kHz mono ``out`` (cached)."""
    if not out.exists():
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
               "-vn", "-ar", "16000", "-ac", "1", str(out)]
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    return out.exists()


def video_gap(audio_path) -> float | None:
    """Onset cross-correlation gap between the audio and its sibling video, in seconds.

    ``None`` when there is no sibling video file or its audio track cannot be decoded.
    ffmpeg runs only inside this function (tests never need it).
    """
    audio_path = Path(audio_path)
    video = find_video(audio_path)
    if video is None:
        return None
    from .audio import work_dir

    wd = work_dir(audio_path)
    wav_a = wd / "audio.mono16000.wav"
    wav_v = wd / f"{video.stem}.video.mono16000.wav"
    if not _decode_mono16k(audio_path, wav_a) or not _decode_mono16k(video, wav_v):
        return None
    import soundfile as sf

    wave_a, sr = sf.read(wav_a, dtype="float64")
    wave_v, _ = sf.read(wav_v, dtype="float64")
    env_a = onset_envelope(np.asarray(wave_a).ravel(), sr)
    env_b = onset_envelope(np.asarray(wave_v).ravel(), sr)
    return best_lag(env_a, env_b, sr)


def apply_videogap(song, audio_path, info: dict | None = None) -> None:
    """Set ``#VIDEOGAP`` on ``song`` when ULTRAAI_VIDEOGAP=1 and a sibling video exists.

    No video (or the option off) -> no header, the .txt is unchanged.
    """
    if not config.get_bool("videogap"):
        return
    gap = video_gap(audio_path)
    if gap is None:
        return
    song.set("VIDEOGAP", f"{gap:.2f}")
    if info is not None:
        info["videogap"] = round(gap, 3)
