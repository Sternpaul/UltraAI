"""Wiring of PR-15: YouTube subs in the lyrics chain, anchors, sidecar fields.

No network: every ytsubs function is monkeypatched; yt-dlp is never invoked.
"""

import json

import numpy as np
import pytest

from ultraai import batch, ytsubs
from ultraai.lrc import LrcResult
from ultraai.transcribe import AsrSegment, AsrWord
from ultraai.ultrastar.model import Song

YTID = "abcdefghijk"  # 11 chars
YTID2 = "ABCDEFGHIJ1"

SUB_LINES = ["one two", "three four", "five six"]
SUB_TIMES = [1.0, 5.0, 9.0]


def _patch_subs(monkeypatch, lines=SUB_LINES, times=SUB_TIMES):
    """Monkeypatch the ytsubs functions the wiring uses; returns a call recorder."""
    calls = {"sub_lines": 0, "sub_segments": 0, "fetch_subs": 0, "resolve_ytid": 0}

    def fake_sub_lines(ytid):
        calls["sub_lines"] += 1
        assert ytid == YTID
        return list(lines), list(times)

    def fake_sub_segments(ytid):
        calls["sub_segments"] += 1
        segs = []
        for t, line in zip(times, lines):
            ws = [ytsubs.SubWord(t + 0.1 * i, t + 0.1 * i + 0.4, w)
                  for i, w in enumerate(line.split())]
            segs.append(ytsubs.SubSegment(t, t + 1.0, line, ws))
        return segs

    def fake_fetch_subs(ytid, manual=True):
        calls["fetch_subs"] += 1
        return {"events": [{"tStartMs": int(t * 1000), "dDurationMs": 1000,
                            "segs": [{"utf8": w, "tStartMs": int(t * 1000), "dDurationMs": 400}]}
                           for t, line in zip(times, lines) for w in line.split()]}

    def fake_resolve(query):
        calls["resolve_ytid"] += 1
        return YTID

    monkeypatch.setattr(ytsubs, "sub_lines", fake_sub_lines)
    monkeypatch.setattr(ytsubs, "sub_segments", fake_sub_segments)
    monkeypatch.setattr(ytsubs, "fetch_subs", fake_fetch_subs)
    monkeypatch.setattr(ytsubs, "resolve_ytid", fake_resolve)
    return calls


def _track(tmp_path, stem="Artist - Title"):
    p = tmp_path / f"{stem}.mp3"
    p.write_bytes(b"\x00")
    return batch.Track(p, "Artist", "Title")


def _lrclib(monkeypatch, n=1):
    res = [LrcResult("Artist", "Title", 200.0, [], [f"lrc line {i}" for i in range(n)])]
    monkeypatch.setattr(batch, "fetch_candidates", lambda *a, **k: res)
    return res


# ------------------------------------------------------------------ (a) chain order
def test_find_lyrics_prefers_sidecar_over_yt_and_lrclib(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    (tmp_path / "Artist - Title.lrc").write_text("[00:01.00]sidecar line\n", encoding="utf-8")
    calls = _patch_subs(monkeypatch)
    _lrclib(monkeypatch)
    cands, source, times = batch.find_lyrics(track)
    assert source == "sidecar.lrc"
    assert times is None
    assert calls["sub_lines"] == 0  # subs not consulted when a sidecar exists


def test_find_lyrics_yt_subs_when_ytid_sidecar_exists(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    calls = _patch_subs(monkeypatch)
    monkeypatch.setattr(batch, "fetch_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LRCLIB must not be called")))
    cands, source, times = batch.find_lyrics(track)
    assert source == "yt"
    assert times == SUB_TIMES
    assert calls["sub_lines"] == 1
    assert len(cands) == 1 and cands[0].synced
    assert cands[0].texts == SUB_LINES
    assert [ln.time for ln in cands[0].lines] == SUB_TIMES


def test_find_lyrics_falls_back_to_lrclib_without_ytid(tmp_path, monkeypatch):
    track = _track(tmp_path)
    calls = _patch_subs(monkeypatch)
    _lrclib(monkeypatch)
    cands, source, times = batch.find_lyrics(track)
    assert source.startswith("lrclib")
    assert times is None
    assert calls["sub_lines"] == 0  # no ytid -> subs never consulted


def test_find_lyrics_yt_anchor_off_gives_plain_lines_no_times(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_YT_ANCHOR", "0")
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    _patch_subs(monkeypatch)
    cands, source, times = batch.find_lyrics(track)
    assert source == "yt"
    assert times is None
    assert cands[0].texts == SUB_LINES and not cands[0].synced


# ------------------------------------------------------------------ (b) anchors passed on
def test_convert_passes_sub_line_times_to_generate_sung(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    _patch_subs(monkeypatch)
    captured = {}

    def fake_generate_sung(audio, cands, language, meta, separation=None, line_times=None):
        captured["line_times"] = line_times
        captured["cands"] = cands
        info = {"candidate": 0, "candidates": 1, "structure": "lrc-confirmed", "anchored": True,
                "asr_word_list": [AsrWord(1.0, 1.4, "one", 0.9), AsrWord(5.0, 5.4, "three", 0.9)]}
        return Song(), info

    monkeypatch.setattr("ultraai.generate.generate_sung", fake_generate_sung)
    monkeypatch.setattr("ultraai.ultrastar.writer.write", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.byproducts.write_byproducts", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.ultrastar.validator.validate", lambda *a, **k: [])
    row = batch.convert(track, tmp_path / "out", "melband", None, False, False)
    assert row["status"] == "ok"
    assert captured["line_times"] == SUB_TIMES  # sub line times passed as anchors
    assert captured["cands"][0].synced


def test_generate_sung_uses_line_times_as_anchors(tmp_path, monkeypatch):
    from ultraai import generate
    from ultraai.lrc import LrcLine

    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    # sub line times offset from where the transcript hears the lines
    sub_times = [2.0, 6.0, 10.0]
    synced = LrcResult("A", "T", 0.0, [LrcLine(t, ln) for t, ln in zip(sub_times, SUB_LINES)], SUB_LINES)
    plain = LrcResult("A", "T", 0.0, [], list(SUB_LINES))

    class FakeAnalysis:
        vocals = tmp_path / "vocals.wav"
        wave = np.zeros(16000 * 30, dtype=np.float32)
        emissions = None
        pitch = None

    monkeypatch.setattr(generate, "analyse", lambda *a, **k: FakeAnalysis())
    # the no-line_times case asks ffprobe for the audio duration (CI has no ffprobe)
    from ultraai import lrc
    monkeypatch.setattr(lrc, "audio_duration", lambda p: 30.0)
    asr = [AsrSegment(t, t + 1.0, ln, [AsrWord(t, t + 0.5, w, 0.9) for w in ln.split()])
           for t, ln in zip(SUB_TIMES, SUB_LINES)]
    monkeypatch.setattr("ultraai.transcribe.transcribe", lambda *a, **k: asr)
    captured = {}

    def fake_gfl(audio, texts, times, language, meta, separation=None, **kw):
        captured["texts"] = list(texts)
        captured["times"] = times
        return Song(), {"anchored": True}

    monkeypatch.setattr(generate, "generate_from_lines", fake_gfl)
    # synced candidate + line_times: the sub times reach the anchored alignment unchanged
    song, info = generate.generate_sung(audio, [synced], "en", {}, line_times=sub_times)
    assert captured["times"] == sub_times
    # the transcript words ride along in info as JSON-serializable triples (sidecar metric)
    assert info["asr_word_list"] == [[1.0, 1.5, "one"], [1.0, 1.5, "two"], [5.0, 5.5, "three"],
                                     [5.0, 5.5, "four"], [9.0, 9.5, "five"], [9.0, 9.5, "six"]]
    json.dumps(info)
    # even a plain (unsynced) candidate is anchored when line_times are passed explicitly
    generate.generate_sung(audio, [plain], "en", {}, line_times=sub_times)
    assert captured["times"] == sub_times
    # without line_times a plain candidate is timed from the transcript, not the subs
    generate.generate_sung(audio, [plain], "en", {})
    assert captured["times"] == SUB_TIMES


# ------------------------------------------------------------------ (c) yt_subs off
def test_yt_subs_off_never_consults_subs(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_YT_SUBS", "0")
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")

    def boom(*a, **k):
        raise AssertionError("subs must not be consulted when yt_subs is off")

    monkeypatch.setattr(ytsubs, "sub_lines", boom)
    monkeypatch.setattr(ytsubs, "sub_segments", boom)
    _lrclib(monkeypatch)
    assert batch.find_ytid(track.path, {"ARTIST": "Artist", "TITLE": "Title"}) is None
    cands, source, times = batch.find_lyrics(track)
    assert source.startswith("lrclib") and times is None
    assert batch.yt_sidecar_fields(track.path, {}, []) == {}


# ------------------------------------------------------------------ (d) search opt-in
def test_search_only_when_yt_subs_search_on(tmp_path, monkeypatch):
    track = _track(tmp_path)
    calls = _patch_subs(monkeypatch)
    meta = {"ARTIST": "Artist", "TITLE": "Title"}
    assert batch.find_ytid(track.path, meta) is None
    assert calls["resolve_ytid"] == 0  # off by default: no search

    monkeypatch.setenv("ULTRAAI_YT_SUBS_SEARCH", "1")
    assert batch.find_ytid(track.path, meta) == YTID
    assert calls["resolve_ytid"] == 1
    side = track.path.with_suffix(".ytid")  # search result cached as the sidecar
    assert side.exists() and side.read_text(encoding="utf-8").strip() == YTID
    assert batch.find_ytid(track.path, meta) == YTID
    assert calls["resolve_ytid"] == 1  # second call reads the sidecar, no new search


# ------------------------------------------------------------------ (e) sidecar fields
def test_sidecar_has_yt_fields_when_subs_and_asr_available(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    _patch_subs(monkeypatch)
    asr_words = [[1.0, 1.4, "one"], [1.4, 1.8, "two"], [5.0, 5.4, "three"], [5.4, 5.8, "four"],
                 [9.0, 9.4, "five"], [9.4, 9.8, "six"]]  # production shape: [start, end, text]

    def fake_generate_sung(audio, cands, language, meta, separation=None, line_times=None):
        return Song(), {"candidate": 0, "candidates": 1, "structure": "lrc-confirmed",
                        "anchored": True, "asr_word_list": asr_words}

    monkeypatch.setattr("ultraai.generate.generate_sung", fake_generate_sung)
    monkeypatch.setattr("ultraai.ultrastar.writer.write", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.byproducts.write_byproducts", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.ultrastar.validator.validate", lambda *a, **k: [])
    batch.convert(track, tmp_path / "out", "melband", None, False, False)
    side = json.loads((tmp_path / "out" / "Artist - Title" / "Artist - Title.ultraai.json")
                      .read_text(encoding="utf-8"))
    assert side["ytid"] == YTID
    assert side["yt_source"] == "manual"
    assert side["yt_agreement"] == pytest.approx(1.0)  # identical token multisets


def test_sidecar_yt_agreement_absent_without_asr_words(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")
    _patch_subs(monkeypatch)

    def fake_generate_sung(audio, cands, language, meta, separation=None, line_times=None):
        return Song(), {"candidate": 0, "candidates": 1, "structure": "lrc", "anchored": False}

    monkeypatch.setattr("ultraai.generate.generate_sung", fake_generate_sung)
    monkeypatch.setattr("ultraai.ultrastar.writer.write", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.byproducts.write_byproducts", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.ultrastar.validator.validate", lambda *a, **k: [])
    batch.convert(track, tmp_path / "out", "melband", None, False, False)
    side = json.loads((tmp_path / "out" / "Artist - Title" / "Artist - Title.ultraai.json")
                      .read_text(encoding="utf-8"))
    assert side["ytid"] == YTID and side["yt_source"] in ("manual", "auto")
    assert "yt_agreement" not in side  # no transcript available -> no metric


def test_sidecar_has_no_yt_fields_without_ytid(tmp_path, monkeypatch):
    track = _track(tmp_path)
    _patch_subs(monkeypatch)
    _lrclib(monkeypatch)

    def fake_generate_sung(audio, cands, language, meta, separation=None, line_times=None):
        return Song(), {"candidate": 0, "candidates": 1, "structure": "lrc", "anchored": False,
                        "asr_word_list": [AsrWord(0.0, 0.5, "lrc", 0.9)]}

    monkeypatch.setattr("ultraai.generate.generate_sung", fake_generate_sung)
    monkeypatch.setattr("ultraai.ultrastar.writer.write", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.byproducts.write_byproducts", lambda *a, **k: None)
    monkeypatch.setattr("ultraai.ultrastar.validator.validate", lambda *a, **k: [])
    batch.convert(track, tmp_path / "out", "melband", None, False, False)
    side = json.loads((tmp_path / "out" / "Artist - Title" / "Artist - Title.ultraai.json")
                      .read_text(encoding="utf-8"))
    assert not any(k.startswith("yt") for k in side)


def test_yt_sidecar_fields_never_raises_on_broken_cache(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text(YTID + "\n")

    def boom(*a, **k):
        raise RuntimeError("broken")

    monkeypatch.setattr(ytsubs, "sub_segments", boom)
    assert batch.yt_sidecar_fields(track.path, {"ARTIST": "Artist", "TITLE": "Title"}, [1]) == {}


# ------------------------------------------------------------------ (f) find_ytid sources
def test_find_ytid_reads_video_header_and_prefers_sidecar_file(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.txt").write_text(
        "#TITLE:x\n#VIDEO:v=AAAAAAAAAAA\n#ARTIST:y\n", encoding="utf-8")
    assert batch.find_ytid(track.path, {}) == "AAAAAAAAAAA"
    (tmp_path / "Artist - Title.ytid").write_text(YTID2 + "\n")
    assert batch.find_ytid(track.path, {}) == YTID2  # file wins over the header
    # explicit metadata (e.g. a job spec) is honoured when no file is present
    sub = tmp_path / "sub"
    sub.mkdir()
    other = _track(sub, "Other - Song")
    assert batch.find_ytid(other.path, {"YTID": YTID}) == YTID
    assert batch.find_ytid(other.path, {"SOURCE": "https://youtu.be/" + YTID}) == YTID


def test_find_ytid_ignores_malformed_sidecar(tmp_path, monkeypatch):
    track = _track(tmp_path)
    (tmp_path / "Artist - Title.ytid").write_text("not an id\n")
    (tmp_path / "Artist - Title.txt").write_text("#VIDEO:v=AAAAAAAAAAA\n", encoding="utf-8")
    assert batch.find_ytid(track.path, {}) == "AAAAAAAAAAA"  # falls through to the header


# ------------------------------------------------------------------ studio importer
def test_import_youtube_writes_ytid_sidecar(tmp_path, monkeypatch):
    from ultraai.studio import importer
    from ultraai.studio.store import Library

    lib = Library(tmp_path / "lib")
    url = "https://www.youtube.com/watch?v=UbstNV2FUNY&list=x"
    monkeypatch.setattr(importer, "youtube_info",
                        lambda u: {"id": "UbstNV2FUNY", "artist": "Artist", "track": "Title",
                                   "uploader": "chan"})
    fake = tmp_path / "dl.m4a"
    fake.write_bytes(b"\x00")
    monkeypatch.setattr(importer, "_download", lambda url, fmt, dest, hook=None: fake)
    monkeypatch.setattr(importer, "remux_audio", lambda p: p)

    class Settings:
        youtube_audio = "m4a"
        youtube_video = "none"
        youtube_cover = False

    st = importer.import_youtube(lib, url, settings=Settings())
    folder = lib.folder(st.id)
    side = (folder / st.audio).with_suffix(".ytid")
    assert side.exists() and side.read_text(encoding="utf-8").strip() == "UbstNV2FUNY"
    # the sidecar is what find_ytid picks up for this audio
    assert batch.find_ytid(folder / st.audio, {}) == "UbstNV2FUNY"


def test_find_ytid_usdb_and_videourl(tmp_path, monkeypatch):
    import json as _json

    from ultraai import batch
    from ultraai import config as _c
    monkeypatch.setattr(_c, "get_bool", lambda k, d=False: True if k == "yt_subs" else d)
    audio = tmp_path / "Song.mp3"
    audio.write_bytes(b"x")
    (tmp_path / "Song.txt").write_text("#TITLE:Song\n#VIDEOURL:https://www.youtube.com/watch?v=ABCDEFGH123\n", encoding="utf-8")
    assert batch.find_ytid(audio, {}) == "ABCDEFGH123"
    (tmp_path / "Song.txt").write_text("#TITLE:Song\n", encoding="utf-8")
    (tmp_path / "x.usdb").write_text(_json.dumps({"meta_tags": "v=12345678901,co=http://x"}), encoding="utf-8")
    assert batch.find_ytid(audio, {}) == "12345678901"
    (tmp_path / "x.usdb").unlink()
    assert batch.find_ytid(audio, {}) is None
