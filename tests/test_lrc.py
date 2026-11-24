"""LRC parsing and the on-disk LRCLIB cache in ultraai/lrc.py (HTTP monkeypatched, no network)."""

import json
import urllib.request

import ultraai.lrc as lrc


def test_parse_lrc_synced():
    text = ("[ti:Some Title]\n[ar:Artist]\n"
            "[00:12.50]Hello world\n"
            "[01:03.00]Line two\n"
            "[00:01.00][00:05.00]repeat me\n"             # one line, two stamps
            "[00:10.00]\n"                                # empty: dropped
            "[00:20.00]♪ ♫\n"                             # music only: dropped
            "a plain line without a stamp\n")
    lines = lrc.parse_lrc(text)
    assert [(ln.time, ln.text) for ln in lines] == [
        (1.0, "repeat me"), (5.0, "repeat me"), (12.5, "Hello world"), (63.0, "Line two")]


def test_parse_lrc_unsynced_and_empty():
    assert lrc.parse_lrc("just\nplain\ntext") == []
    assert lrc.parse_lrc("") == []


def test_best_match_prefers_synced_closest_duration():
    results = [
        {"artistName": "A", "trackName": "T", "duration": 100.0,
         "syncedLyrics": "[00:01.00]hello\n"},
        {"artistName": "A", "trackName": "T", "duration": 203.0,
         "syncedLyrics": "[00:02.00]hello\nworld\n"},
        {"artistName": "B", "trackName": "T", "duration": 200.0,
         "plainLyrics": "hello\nworld"},
    ]
    bm = lrc.best_match(results, 200.0)
    assert bm.synced and bm.duration == 203.0             # synced pool wins, closest duration
    assert [(ln.time, ln.text) for ln in bm.lines] == [(2.0, "hello")]
    plain = lrc.best_match([results[2]], None)
    assert not plain.synced and plain.plain == ["hello", "world"]
    assert lrc.best_match([], 100.0) is None


CANNED = [{"artistName": "Artist", "trackName": "Title", "duration": 200.0,
           "syncedLyrics": "[00:01.00]hello\n[00:03.00]world\n", "plainLyrics": "hello\nworld"}]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


def _fake_urlopen(calls, payload):
    def fake(req, timeout=0):
        calls.append(req.full_url)
        return _Resp(payload)
    return fake


def test_lrclib_search_writes_cache_and_reuses_it(monkeypatch, tmp_path):
    monkeypatch.setattr(lrc, "CACHE_DIR", tmp_path)       # CACHE_DIR was bound at import; patch the use site
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(calls, json.dumps(CANNED).encode()))
    res = lrc.search("Artist", "Title")
    assert res == CANNED and len(calls) == 1
    assert "artist_name=Artist" in calls[0] and "track_name=Title" in calls[0]
    files = list((tmp_path / "lrclib").glob("*.json"))
    assert [f.name for f in files] == ["Artist_Title.json"]
    assert json.loads(files[0].read_text()) == CANNED
    # second call is served from the cache: no HTTP
    assert lrc.search("Artist", "Title") == CANNED
    assert len(calls) == 1


def test_lrclib_query_cache_naming_for_other_params(monkeypatch, tmp_path):
    monkeypatch.setattr(lrc, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(calls, b"[]"))
    assert lrc._query({"track_name": "Title"}) == []
    assert len(calls) == 1 and "track_name=Title" in calls[0]
    assert [f.name for f in (tmp_path / "lrclib").glob("*.json")] == ["q_track_name_Title.json"]
    lrc._query({"track_name": "Title"})
    assert len(calls) == 1                                # cache hit, no second request
