"""Audio helpers: decoding, vocal separation (cached), loading as mono float arrays."""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

from . import config  # noqa: E402

CACHE_DIR = Path(config.get("cache") or Path.home() / ".cache" / "ultraai")  # read once at import
MODEL_DIR = CACHE_DIR / "models"

# Separation models (audio-separator names). Quality vs. CPU time trade-off.
SEPARATION_MODELS = {
    "htdemucs": "htdemucs.yaml",
    "htdemucs_ft": "htdemucs_ft.yaml",
    "melband": "vocals_mel_band_roformer.ckpt",
    "bsroformer": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
}
DEFAULT_SEPARATION = "htdemucs"  # 23-song depth pass: separation barely matters, htdemucs is fastest
SEPARATION_NAMES = {"htdemucs": "HTDemucs", "htdemucs_ft": "HTDemucs fine-tuned",
                    "melband": "MelBand RoFormer"}
LEAD_NAMES = {"kara2": "UVR MDX Karaoke 2", "melkara": "MelBand RoFormer Karaoke"}


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()[:16]


def work_dir(audio: Path) -> Path:
    d = CACHE_DIR / "work" / file_hash(audio)
    d.mkdir(parents=True, exist_ok=True)
    return d


def to_wav(audio: Path, out: Path, sr: int = 44100, mono: bool = False) -> Path:
    """Decode any format with ffmpeg."""
    if not out.exists():
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(audio), "-ar", str(sr)]
        if mono:
            cmd += ["-ac", "1"]
        subprocess.run(cmd + [str(out)], check=True)
    return out


def separate(audio: Path, model: str = DEFAULT_SEPARATION, need_instrumental: bool = True) -> tuple[Path, Path]:
    """Return (vocals.wav, instrumental.wav), computing them once per audio file and model.

    ``need_instrumental=False``: the cached vocals suffice even if the (large) instrumental
    stem was deleted to save disk space.
    """
    wd = work_dir(audio)
    vocals, instr = wd / f"vocals.{model}.wav", wd / f"instrumental.{model}.wav"
    if vocals.exists() and (instr.exists() or not need_instrumental):
        return vocals, instr

    from audio_separator.separator import Separator  # heavy import

    src = to_wav(audio, wd / "mix.wav")
    tmp = wd / f"sep_{model}"
    tmp.mkdir(exist_ok=True)
    sep = Separator(output_dir=str(tmp), model_file_dir=str(MODEL_DIR), log_level=logging.WARNING)
    sep.load_model(model_filename=SEPARATION_MODELS.get(model, model))
    outputs = [tmp / Path(p).name for p in sep.separate(str(src))]
    for p in outputs:
        name = p.name.lower()
        if "(vocals)" in name:
            shutil.move(p, vocals)
        elif "(instrumental)" in name or "(other)" in name or "(no_vocals)" in name:
            shutil.move(p, instr)
    if not instr.exists():  # demucs gives 4 stems: build instrumental = mix - vocals
        mix, sr = sf.read(src, always_2d=True)
        voc, _ = sf.read(vocals, always_2d=True)
        n = min(len(mix), len(voc))
        sf.write(instr, mix[:n] - voc[:n], sr)
    shutil.rmtree(tmp, ignore_errors=True)
    return vocals, instr


def load_mono(path: Path, sr: int) -> np.ndarray:
    """Load as mono float32 at ``sr`` (uses ffmpeg for resampling)."""
    wd = path.parent if path.suffix == ".wav" else work_dir(path)
    tmp = wd / f"{path.stem}.mono{sr}.wav"
    to_wav(path, tmp, sr=sr, mono=True)
    data, file_sr = sf.read(tmp, dtype="float32")
    assert file_sr == sr
    return data


LEAD_MODELS = {
    "kara2": "UVR_MDXNET_KARA_2.onnx",
    "hp5": "5_HP-Karaoke-UVR.pth",
    "melkara": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
}


def lead_vocals(vocals: Path, model: str = "kara2") -> tuple[Path, Path]:
    """Split a vocal stem into (lead, backing) with a karaoke model. Cached next to the stem."""
    lead = vocals.with_name(vocals.stem + f".lead-{model}.wav")
    backing = vocals.with_name(vocals.stem + f".backing-{model}.wav")
    if lead.exists() and backing.exists():
        return lead, backing
    from audio_separator.separator import Separator

    tmp = vocals.parent / f"lead_{model}"
    tmp.mkdir(exist_ok=True)
    sep = Separator(output_dir=str(tmp), model_file_dir=str(MODEL_DIR), log_level=logging.WARNING)
    sep.load_model(model_filename=LEAD_MODELS.get(model, model))
    for p in [tmp / Path(p).name for p in sep.separate(str(vocals))]:
        name = p.name.lower()
        if "(vocals)" in name:
            shutil.move(p, lead)
        else:
            shutil.move(p, backing)
    shutil.rmtree(tmp, ignore_errors=True)
    return lead, backing
