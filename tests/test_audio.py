"""Audio cache helpers in ultraai/audio.py: file_hash, work_dir layout, and the
cache-hit paths of separate()/load_mono with a fake Separator (no model, no ffmpeg)."""

import hashlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import ultraai.audio as audio


def test_file_hash_stable_and_content_dependent(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello!")
    h = audio.file_hash(a)
    assert h == hashlib.sha1(b"hello").hexdigest()[:16]
    assert audio.file_hash(a) == h                        # stable across calls
    assert audio.file_hash(b) != h                        # changes with content


def test_work_dir_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path)     # CACHE_DIR is read once at import
    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio bytes")
    wd = audio.work_dir(song)
    assert wd == tmp_path / "work" / audio.file_hash(song)
    assert wd.is_dir()
    assert audio.work_dir(song) == wd                     # idempotent


class _FakeSeparator:
    """Stands in for audio_separator's Separator: writes a vocals + instrumental stem."""

    calls = 0

    def __init__(self, output_dir, model_file_dir, log_level):
        self.output_dir = output_dir

    def load_model(self, model_filename):
        self.model = model_filename

    def separate(self, src):
        type(self).calls += 1
        out = []
        for tag in ("Vocals", "Instrumental"):
            p = Path(self.output_dir) / f"mix_({tag}).wav"
            sf.write(p, np.zeros(1600, dtype="float32"), 44100)
            out.append(str(p))
        return out


@pytest.fixture
def fake_separator(monkeypatch):
    _FakeSeparator.calls = 0
    mod = types.ModuleType("audio_separator.separator")
    mod.Separator = _FakeSeparator
    monkeypatch.setitem(sys.modules, "audio_separator", types.ModuleType("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", mod)
    return _FakeSeparator


def test_separate_caches_per_audio_and_model(monkeypatch, tmp_path, fake_separator):
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path)
    song = tmp_path / "song.mp3"
    song.write_bytes(b"audio bytes")
    wd = audio.work_dir(song)
    (wd / "mix.wav").write_bytes(b"pre-made mix")         # to_wav skips ffmpeg when the file exists
    vocals, instr = audio.separate(song, "melband")
    assert fake_separator.calls == 1
    assert vocals == wd / "vocals.melband.wav" and vocals.exists()
    assert instr == wd / "instrumental.melband.wav" and instr.exists()
    # cache hit: both stems exist -> no recompute
    assert audio.separate(song, "melband") == (vocals, instr)
    assert fake_separator.calls == 1
    # the vocals alone suffice when the instrumental was deleted to save disk
    instr.unlink()
    v2, i2 = audio.separate(song, "melband", need_instrumental=False)
    assert v2 == vocals and not i2.exists()
    assert fake_separator.calls == 1
    # ... but asking for the instrumental recomputes both
    v3, i3 = audio.separate(song, "melband", need_instrumental=True)
    assert i3.exists() and fake_separator.calls == 2
    # another model gets its own files
    audio.separate(song, "htdemucs")
    assert (wd / "vocals.htdemucs.wav").exists()
    assert fake_separator.calls == 3


def test_load_mono_uses_the_cached_mono_file(monkeypatch, tmp_path):
    voc = tmp_path / "vocals.wav"
    data = (0.5 * np.sin(np.arange(1600) / 10.0)).astype("float32")
    sf.write(voc, data, 16000, subtype="FLOAT")
    mono = tmp_path / "vocals.mono16000.wav"
    sf.write(mono, data, 16000, subtype="FLOAT")
    runs = []

    def no_ffmpeg(cmd, **kw):
        runs.append(cmd)
        raise AssertionError("ffmpeg must not run when the mono file is cached")

    monkeypatch.setattr(audio, "subprocess", types.SimpleNamespace(run=no_ffmpeg))
    out = audio.load_mono(voc, 16000)
    assert len(runs) == 0 and np.allclose(out, data)

    # without the cached mono file, decoding (ffmpeg) is invoked once
    mono.unlink()

    def fake_ffmpeg(cmd, **kw):
        runs.append(cmd)
        sf.write(cmd[-1], data, 16000, subtype="FLOAT")   # pretend ffmpeg wrote the resampled file
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(audio, "subprocess", types.SimpleNamespace(run=fake_ffmpeg))
    out = audio.load_mono(voc, 16000)
    assert len(runs) == 1 and np.allclose(out, data)
