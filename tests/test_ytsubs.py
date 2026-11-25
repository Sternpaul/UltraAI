"""ytsubs.py: json3 parsing, cache, failure handling, manual-over-auto, agreement.

No real network: every yt-dlp invocation goes through ytsubs._run, monkeypatched
here to write fake subtitle files into the tmp dir given via -o.
"""

import json

import pytest

from ultraai import config, ytsubs
from ultraai.transcribe import AsrWord


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ytsubs, "CACHE_DIR", tmp_path)
    return tmp_path


def _ev(t_ms, d_ms, segs):
    return {"tStartMs": t_ms, "dDurationMs": d_ms, "segs": segs}


def _seg(text, t_ms=None, d_ms=100):
    s = {"utf8": text, "dDurationMs": d_ms}
    if t_ms is not None:
        s["tStartMs"] = t_ms
    return s


SAMPLE = {
    "events": [
        _ev(1000, 1000, [_seg("Hello ", 1000, 400), _seg("world", 1400, 600)]),
        _ev(5000, 800, [_seg("[Music]", 5000, 800)]),          # pure music event: dropped
        _ev(9000, 900, [_seg("la ", 9000, 300), _seg("la (x2)", 9300, 600)]),
    ]
}


def _write_cache(cache_dir, ytid, data):
    d = cache_dir / "yt"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ytid}.json").write_text(json.dumps(data), encoding="utf-8")


def _fake_run_factory(calls, files):
    """A fake _run: writes `files` (name -> dict) as json3 into the -o tmp dir."""
    def fake(cmd):
        calls.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        import os
        for name, data in files.items():
            with open(os.path.join(os.path.dirname(out), name), "w", encoding="utf-8") as f:
                f.write(json.dumps(data))
        return ""
    return fake


def test_json3_parsing_word_times_seconds_and_music_dropped(cache_dir):
    _write_cache(cache_dir, "vid1", SAMPLE)
    segs = ytsubs.sub_segments("vid1")
    assert [(s.start, s.end, s.text) for s in segs] == [
        (1.0, 2.0, "Hello world"),
        (9.0, 9.9, "la la (x2)"),
    ]
    w = segs[0].words
    assert [(x.start, x.end, x.text) for x in w] == [(1.0, 1.4, "Hello"), (1.4, 2.0, "world")]


def test_json3_seg_without_tstart_uses_event_start_plus_offset(cache_dir):
    data = {"events": [_ev(2000, 500, [_seg("a ", d_ms=200), _seg("b", d_ms=300)])]}
    _write_cache(cache_dir, "vid2", data)
    (seg,) = ytsubs.sub_segments("vid2")
    assert [(w.start, w.end, w.text) for w in seg.words] == [(2.0, 2.2, "a"), (2.2, 2.5, "b")]


def test_cache_write_and_read_second_call_skips_run(cache_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {"sub.en.json3": SAMPLE}))
    first = ytsubs.fetch_subs("vid3")
    assert first == SAMPLE
    assert (cache_dir / "yt" / "vid3.json").exists()
    assert len(calls) == 1
    assert "--write-subs" in calls[0] and "--write-auto-subs" in calls[0]
    second = ytsubs.fetch_subs("vid3")
    assert second == SAMPLE
    assert len(calls) == 1  # served from cache, _run not invoked again


def test_fetch_failure_returns_none_and_does_not_raise(cache_dir, monkeypatch):
    def boom(cmd):
        raise RuntimeError("yt-dlp missing")
    monkeypatch.setattr(ytsubs, "_run", boom)
    assert ytsubs.fetch_subs("vid4") is None
    assert not (cache_dir / "yt" / "vid4.json").exists()
    assert ytsubs.sub_segments("vid4") == []
    assert ytsubs.sub_lines("vid4") == ([], [])


def test_no_sub_files_returns_none(cache_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {}))
    assert ytsubs.fetch_subs("vid5") is None


def test_manual_sub_preferred_over_auto(cache_dir, monkeypatch):
    manual = {"events": [_ev(0, 100, [_seg("real lyrics", 0, 100)])]}
    auto = {"events": [_ev(0, 100, [_seg("auto caption", 0, 100)])]}
    calls = []
    monkeypatch.setattr(ytsubs, "_run",
                        _fake_run_factory(calls, {"sub.en.json3": manual, "sub.en.auto.json3": auto}))
    assert ytsubs.fetch_subs("vid6") == manual


def test_sub_lines_times_are_first_word_start_and_cleaned(cache_dir):
    _write_cache(cache_dir, "vid7", SAMPLE)
    lines, times = ytsubs.sub_lines("vid7")
    assert times == [1.0, 9.0, 9.0]          # "(x2)" expanded by clean_lines, time kept
    assert lines == ["Hello world", "la la", "la la"]


def test_word_agreement_identical_disjoint_half():
    subs = [ytsubs.SubWord(0, 1, "Hello"), ytsubs.SubWord(1, 2, "world")]
    assert ytsubs.word_agreement([AsrWord(0, 1, "hello", 1.0), AsrWord(1, 2, "world", 1.0)], subs) == 1.0
    assert ytsubs.word_agreement([AsrWord(0, 1, "foo", 1.0)], subs) == 0.0
    # multiset Jaccard pinned: A={hello}, B={hello, world} -> 1/2
    assert ytsubs.word_agreement([AsrWord(0, 1, "hello", 1.0)], subs) == 0.5
    # tuples (start, end, text) accepted too, punctuation/case normalized
    assert ytsubs.word_agreement([(0.0, 1.0, "Hello!")], [ytsubs.SubWord(0, 1, "hello")]) == 1.0
    assert ytsubs.word_agreement([], []) == 0.0


def test_config_defaults(monkeypatch):
    for key, env, default in [("yt_subs", "ULTRAAI_YT_SUBS", "1"),
                              ("yt_subs_search", "ULTRAAI_YT_SUBS_SEARCH", "0"),
                              ("yt_anchor", "ULTRAAI_YT_ANCHOR", "1")]:
        monkeypatch.delenv(env, raising=False)
        assert config.get(key) == default
        assert config.env_of(key) == env


def test_resolve_ytid_parses_print_id_and_caches(cache_dir, monkeypatch):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        assert cmd[:2] == ["yt-dlp", "--skip-download"]
        assert "--print" in cmd and cmd[-1].startswith("ytsearch1:")
        return "abc123XYZ\n"
    monkeypatch.setattr(ytsubs, "_run", fake)
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    assert len(calls) == 1
    cached = list((cache_dir / "yt").glob("search_*.txt"))
    assert len(cached) == 1 and cached[0].read_text() == "abc123XYZ"
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    assert len(calls) == 1  # cached


def test_resolve_ytid_failure_returns_none(cache_dir, monkeypatch):
    def boom(cmd):
        raise RuntimeError("no network")
    monkeypatch.setattr(ytsubs, "_run", boom)
    assert ytsubs.resolve_ytid("x - y") is None
