"""Fault-injection tests (out/research/infra.md section 4): the error paths around
ffmpeg/ffprobe, the lyrics lookup, the ASR/Whisper step and the on-disk caches.

No real media, no network, no model downloads: subprocess and HTTP are monkeypatched
(every audio.py/lrc.py call goes through the module-level ``subprocess``, every LRCLIB
call through ``urllib.request.urlopen``, every yt-dlp call through ``ytsubs._run``).

Two of these paths are real bugs fixed in the same PR:
- worker: an LRCLIB outage used to kill the Studio job; it now falls through to the
  YouTube-subtitles last resort (order: user > LRCLIB > yt, pr/18);
- transcribe: a corrupt ``*.asr-*.json`` cache used to raise uncaught; it is now a
  cache miss (re-transcribe, never crash the job).
"""

import json
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

import ultraai.audio as audio
import ultraai.batch as batch
import ultraai.lrc as lrc
import ultraai.transcribe as tr
import ultraai.worker as worker
from ultraai.lrc import LrcResult

# ------------------------------------------------------------------ helpers

def _asr_cache(vocals: Path, model: str = "m", lang: str = "auto") -> Path:
    return vocals.with_name(f"{vocals.stem}.asr-{model}-{lang}.json")


def _seg(start=0.0, end=1.0, text=" hello ", word=" hello "):
    w = types.SimpleNamespace(start=start, end=end, word=word, probability=0.9)
    return types.SimpleNamespace(start=start, end=end, text=text, words=[w])


def _patch_model(monkeypatch, calls, segs=None, exc=None):
    """Replace tr._model: transcribe() records the call and returns segs or raises exc."""

    class FakeModel:
        def transcribe(self, wav, **kw):
            calls.append(wav)
            if exc is not None:
                raise exc
            return list(segs or []), None

    monkeypatch.setattr(tr, "_model", lambda name: FakeModel())


class _FakePr:
    """Progress stand-in: records start/skip."""

    def __init__(self):
        self.starts, self.skips = [], []

    def start(self, key):
        self.starts.append(key)

    def skip(self, key, why=""):
        self.skips.append((key, why))


class _DummyLock:
    """worker._Lock without fcntl (absent on Windows test machines)."""

    def __init__(self, path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_job(tmp_path: Path, **spec_over) -> Path:
    job = tmp_path / "job"
    job.mkdir()
    spec = {"id": "j1", "audio": "song.m4a", "artist": "Artist", "title": "Title",
            "separation": "htdemucs",
            "options": {"pitch_check": [], "sep_check": []},  # keep the checks stage off
            **spec_over}
    (job / "job.json").write_text(json.dumps(spec), encoding="utf-8")
    (job / spec["audio"]).write_bytes(b"fake audio bytes")  # unreadable by ffmpeg/soundfile
    return job


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    """Isolate the worker's cache/lock/systemd and keep the job dir in tmp_path."""
    monkeypatch.setattr(worker, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(worker, "RATES_FILE", tmp_path / "cache" / "stage_rates.json")
    monkeypatch.setattr(worker, "_Lock", _DummyLock)
    monkeypatch.setattr(worker, "pause_background", lambda: [])
    monkeypatch.setattr(worker, "resume_background", lambda units: None)
    monkeypatch.setattr(audio, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(lrc, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(lrc, "_last_request", 0.0)
    return tmp_path


def _patch_pipeline(monkeypatch, tmp_path, *, asr=None, asr_exc=None, vocals=None):
    """Mock every heavy step of worker._process except the lyrics lookup itself."""
    monkeypatch.setattr(lrc, "audio_duration", lambda p: 180.0)
    vocals = vocals or (tmp_path / "vocals.htdemucs.wav")
    monkeypatch.setattr(audio, "separate", lambda a, m, need_instrumental=True, **k: (vocals, tmp_path / "i.wav"))
    analysis = types.SimpleNamespace(vocals=vocals, wave=np.zeros(2 * 16000, dtype=np.float32),
                                     emissions=None, pitch=None)
    monkeypatch.setattr("ultraai.generate.analyse", lambda *a, **k: analysis)
    calls = []

    def fake_transcribe(*a, **k):
        calls.append(a)
        if asr_exc is not None:
            raise asr_exc
        return asr if asr is not None else []

    monkeypatch.setattr(tr, "transcribe", fake_transcribe)
    monkeypatch.setattr(worker, "_editor_media", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.batch.yt_sidecar_fields", lambda *a, **k: {})
    monkeypatch.setattr("ultraai.ultrastar.writer.write", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.byproducts.write_byproducts", lambda *a, **k: None)

    class FakeSong:
        def set(self, key, value):
            pass

    info = {"issues": [], "lines": [], "candidate": 0, "structure": "lrc", "asr_coverage": 0.0}
    monkeypatch.setattr("ultraai.generate.generate_sung", lambda *a, **k: (FakeSong(), info))
    monkeypatch.setattr("ultraai.generate.generate_from_lines", lambda *a, **k: (FakeSong(), info))
    return calls


# ------------------------------------------------------------------ 1. ffmpeg / ffprobe failure

def test_to_wav_ffmpeg_missing_raises_and_writes_nothing(monkeypatch, tmp_path):
    def no_ffmpeg(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    monkeypatch.setattr(audio, "subprocess", types.SimpleNamespace(run=no_ffmpeg))
    with pytest.raises(FileNotFoundError):
        audio.to_wav(tmp_path / "song.m4a", tmp_path / "mix.wav")
    assert not (tmp_path / "mix.wav").exists()


def test_to_wav_ffmpeg_rc_nonzero_raises_and_writes_nothing(monkeypatch, tmp_path):
    def failing(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(audio, "subprocess", types.SimpleNamespace(run=failing))
    with pytest.raises(subprocess.CalledProcessError):
        audio.to_wav(tmp_path / "song.m4a", tmp_path / "mix.wav")
    assert not (tmp_path / "mix.wav").exists()


def test_audio_duration_ffprobe_failure_returns_zero(monkeypatch, tmp_path):
    """ffprobe runs but fails (rc!=0, empty stdout): duration 0, callers use their fallback."""
    monkeypatch.setattr(lrc, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(stdout="", returncode=1)))
    assert lrc.audio_duration(tmp_path / "song.mp3") == 0.0


def test_audio_duration_ffprobe_missing_raises(monkeypatch, tmp_path):
    def no_ffprobe(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "ffprobe")

    monkeypatch.setattr(lrc, "subprocess", types.SimpleNamespace(run=no_ffprobe))
    with pytest.raises(FileNotFoundError):
        lrc.audio_duration(tmp_path / "song.mp3")


def test_worker_unreadable_audio_is_a_clean_error_status(worker_env, monkeypatch, tmp_path):
    """Garbage audio bytes -> ffmpeg rc!=0 -> job status "error", not a dead worker."""
    job = _make_job(tmp_path)
    monkeypatch.setattr(lrc, "audio_duration", lambda p: 0.0)  # ffprobe also fails on garbage
    # separate() imports audio_separator before calling to_wav; the stub is never used
    # because to_wav (ffmpeg on the garbage file) fails first
    sep_mod = types.ModuleType("audio_separator.separator")
    sep_mod.Separator = object
    monkeypatch.setitem(sys.modules, "audio_separator", types.ModuleType("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", sep_mod)

    def failing_ffmpeg(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(audio, "subprocess", types.SimpleNamespace(run=failing_ffmpeg))
    with pytest.raises(subprocess.CalledProcessError):
        worker.process(job)
    st = worker.read_status(job)
    assert st["state"] == "error"
    assert "CalledProcessError" in st["error"]


# ------------------------------------------------------------------ 2. LRCLIB down

def test_lrc_query_lrclib_down_raises_after_three_attempts(worker_env, monkeypatch):
    calls = []

    def down(req, timeout=None):
        calls.append(req)
        raise urllib.error.URLError("lrclib.net unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.URLError):
        lrc._query({"artist_name": "Artist", "track_name": "Title"})
    assert len(calls) == 3                      # retried, then gave up
    cache_dir = worker_env / "cache" / "lrclib"
    assert not list(cache_dir.glob("*.json"))   # a failed query is never cached


def test_batch_find_lyrics_lrclib_down_falls_back_to_tags(monkeypatch, tmp_path):
    """batch.find_lyrics already had the try/except: LRCLIB outage -> embedded tags."""
    monkeypatch.setattr(batch, "fetch_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    song = tmp_path / "Artist - Title.mp3"
    song.write_bytes(b"x")
    track = batch.Track(path=song, artist="Artist", title="Title", tag_lyrics="la la\nla")
    cands, source, line_times = batch.find_lyrics(track)
    assert source == "tags" and cands and line_times is None


def test_batch_find_lyrics_lrclib_down_then_yt_last_resort(monkeypatch, tmp_path):
    monkeypatch.setattr(batch, "fetch_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    song = tmp_path / "Artist - Title.mp3"
    song.write_bytes(b"x")
    track = batch.Track(path=song, artist="Artist", title="Title")
    yt = ([LrcResult("Artist", "Title", 0.0, [], ["sub line one"])], None)
    monkeypatch.setattr(batch, "yt_sub_lyrics", lambda *a, **k: yt)
    cands, source, line_times = batch.find_lyrics(track)
    assert source == "yt" and cands[0].texts == ["sub line one"]
    # no subs either -> empty, but no exception
    monkeypatch.setattr(batch, "yt_sub_lyrics", lambda *a, **k: None)
    cands, source, line_times = batch.find_lyrics(track)
    assert cands == [] and source == "none" and line_times is None


@pytest.mark.parametrize("exc", [urllib.error.URLError("lrclib down"), FileNotFoundError("ffprobe"),
                                 ValueError("corrupt lrclib cache")])
def test_worker_lookup_lyrics_failure_falls_back_to_yt(monkeypatch, tmp_path, exc):
    """The worker fix: ANY lyrics-lookup failure falls through to YouTube subs."""
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(lrc, "fetch_candidates", boom)
    yt = ([LrcResult("Artist", "Title", 0.0, [], ["sub line"])], [0.0, 1.5])
    monkeypatch.setattr(batch, "yt_sub_lyrics", lambda *a, **k: yt)
    pr = _FakePr()
    spec = {"artist": "Artist", "title": "Title"}
    cands, source, line_times = worker.lookup_lyrics(spec, tmp_path / "song.m4a",
                                                     {"ARTIST": "Artist", "TITLE": "Title"}, pr)
    assert source == "yt" and cands[0].texts == ["sub line"] and line_times == [0.0, 1.5]
    assert pr.starts == ["lyrics"]


def test_worker_lookup_lyrics_user_first_and_lrclib_ok(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("must not be called")

    pr = _FakePr()
    audio = tmp_path / "song.m4a"
    # user lyrics win; neither LRCLIB nor yt is consulted
    cands, source, line_times = worker.lookup_lyrics(
        {"lyrics": "[00:01.00] my line", "artist": "A", "title": "T"}, audio, {}, pr)
    assert source == "user" and cands[0].texts == ["my line"] and line_times is None
    assert pr.skips == [("lyrics", "your lyrics")]
    # LRCLIB ok -> source LRCLIB, yt not consulted
    monkeypatch.setattr(batch, "yt_sub_lyrics", boom)
    monkeypatch.setattr(lrc, "fetch_candidates",
                        lambda *a, **k: [LrcResult("A", "T", 0.0, [], ["lrc line"])])
    cands, source, line_times = worker.lookup_lyrics({"artist": "A", "title": "T"}, audio, {}, _FakePr())
    assert source == "LRCLIB" and cands[0].texts == ["lrc line"] and line_times is None
    # LRCLIB empty + no subs -> empty, no exception
    monkeypatch.setattr(lrc, "fetch_candidates", lambda *a, **k: [])
    monkeypatch.setattr(batch, "yt_sub_lyrics", lambda *a, **k: None)
    cands, source, line_times = worker.lookup_lyrics({"artist": "A", "title": "T"}, audio, {}, _FakePr())
    assert cands == [] and source == "LRCLIB" and line_times is None


def test_worker_lrclib_outage_does_not_kill_the_job(worker_env, monkeypatch, tmp_path):
    """Full worker path: LRCLIB down -> YouTube subs -> job finishes (state done, source yt)."""
    job = _make_job(tmp_path)
    _patch_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(lrc, "fetch_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("lrclib down")))
    monkeypatch.setattr(batch, "yt_sub_lyrics",
                        lambda *a, **k: ([LrcResult("Artist", "Title", 0.0, [], ["hello world", "bye now"])], None))
    res = worker.process(job)
    assert res["state"] == "done"
    assert worker.read_status(job)["state"] == "done"
    side = json.loads((job / "out" / "song.ultraai.json").read_text(encoding="utf-8"))
    assert side["lyrics_source"] == "yt"


def test_worker_lrclib_outage_and_no_subs_is_needs_lyrics_not_error(worker_env, monkeypatch, tmp_path):
    """LRCLIB down + no yt subs + empty transcript -> clean needs_lyrics, never a crash."""
    job = _make_job(tmp_path)
    _patch_pipeline(monkeypatch, tmp_path, asr=[])  # vad_filter on empty audio yields nothing
    monkeypatch.setattr(lrc, "fetch_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("lrclib down")))
    monkeypatch.setattr(batch, "yt_sub_lyrics", lambda *a, **k: None)
    res = worker.process(job)
    assert res["state"] == "needs_lyrics"
    st = worker.read_status(job)
    assert st["state"] == "needs_lyrics" and st.get("draft") == ""


# ------------------------------------------------------------------ 3. corrupt caches

def test_transcribe_corrupt_asr_cache_is_a_cache_miss(monkeypatch, tmp_path):
    """The transcribe fix: unparseable *.asr-*.json -> re-transcribe, never crash."""
    vocals = tmp_path / "vocals.mono16000.wav"
    vocals.write_bytes(b"fake")
    cache = _asr_cache(vocals)
    cache.write_text("{not json at all", encoding="utf-8")
    calls = []
    _patch_model(monkeypatch, calls, segs=[_seg()])
    segs = tr.transcribe(vocals, None, model="m", wave=np.zeros(16000, dtype=np.float32))
    assert len(calls) == 1 and len(segs) == 1 and segs[0].text == "hello"
    assert segs[0].words[0].text == "hello"
    assert json.loads(cache.read_text(encoding="utf-8"))[0]["text"] == "hello"  # cache rewritten
    # second call: valid cache -> no model call
    assert len(tr.transcribe(vocals, None, model="m", wave=np.zeros(16000, dtype=np.float32))) == 1
    assert len(calls) == 1


def test_transcribe_wrong_shape_asr_cache_is_a_cache_miss(monkeypatch, tmp_path):
    vocals = tmp_path / "vocals.mono16000.wav"
    vocals.write_bytes(b"fake")
    _asr_cache(vocals).write_text(json.dumps({"segments": []}), encoding="utf-8")  # valid json, wrong shape
    calls = []
    _patch_model(monkeypatch, calls, segs=[_seg()])
    assert len(tr.transcribe(vocals, "en", model="m", wave=np.zeros(16000, dtype=np.float32))) == 1
    assert len(calls) == 1


def test_pitch_track_corrupt_cache_raises_catchable(tmp_path):
    """A corrupt *.pitch.npz raises a normal exception (worker.process contains it in
    an "error" status, see the whisper-failure test below); it must not corrupt state."""
    from ultraai.generate import pitch_track

    vocals = tmp_path / "vocals.wav"
    vocals.write_bytes(b"fake")
    vocals.with_suffix(".pitch.npz").write_bytes(b"not an npz")
    with pytest.raises(Exception):
        pitch_track(vocals, np.zeros(1600, dtype=np.float32), "swift")


# ------------------------------------------------------------------ 4. empty audio / whisper failure / unreadable audio

def test_transcribe_empty_audio_vad_yields_nothing_and_caches(monkeypatch, tmp_path):
    """Silent vocals: vad_filter yields no segments -> asr == [] (feeds needs_lyrics)."""
    vocals = tmp_path / "vocals.mono16000.wav"
    vocals.write_bytes(b"fake")
    calls = []
    _patch_model(monkeypatch, calls, segs=[])  # vad_filter drops the silence
    segs = tr.transcribe(vocals, None, model="m", wave=np.zeros(3 * 16000, dtype=np.float32))
    assert segs == []
    assert json.loads(_asr_cache(vocals).read_text(encoding="utf-8")) == []
    assert tr.transcribe(vocals, None, model="m", wave=np.zeros(16000, dtype=np.float32)) == []
    assert len(calls) == 1  # empty result is cached too: no second model call


def test_structure_match_empty_asr_early_returns():
    from ultraai.structure import match

    st = match(["hello world"], [], None)
    assert st.lines == [] and st.asr_words == 0 and st.lyric_lines == ["hello world"]


def test_transcribe_whisper_failure_raises_and_writes_no_cache(monkeypatch, tmp_path):
    vocals = tmp_path / "vocals.mono16000.wav"
    vocals.write_bytes(b"fake")
    calls = []
    _patch_model(monkeypatch, calls, exc=MemoryError("CUDA out of memory"))
    with pytest.raises(MemoryError):
        tr.transcribe(vocals, None, model="m", wave=np.zeros(16000, dtype=np.float32))
    assert len(calls) == 1
    assert not list(tmp_path.glob("*.asr-*.json"))  # no partial/corrupt cache left behind


def test_transcribe_unreadable_audio_file_raises_and_writes_no_cache(tmp_path):
    garbage = tmp_path / "vocals.mono16000.wav"
    garbage.write_bytes(b"this is not a wav file")
    with pytest.raises(Exception):  # soundfile cannot parse it
        tr.transcribe(garbage, None, model="m")  # wave=None -> sf.read on the garbage file
    assert not list(tmp_path.glob("*.asr-*.json"))


def test_worker_whisper_failure_is_a_clean_error_status(worker_env, monkeypatch, tmp_path):
    job = _make_job(tmp_path)
    _patch_pipeline(monkeypatch, tmp_path, asr_exc=RuntimeError("whisper exploded"))
    monkeypatch.setattr(lrc, "fetch_candidates",
                        lambda *a, **k: [LrcResult("Artist", "Title", 0.0, [], ["hello world"])])
    with pytest.raises(RuntimeError, match="whisper exploded"):
        worker.process(job)
    st = worker.read_status(job)
    assert st["state"] == "error" and "RuntimeError: whisper exploded" in st["error"]
