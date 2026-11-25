"""LRC parsing and best-match selection in ultraai/lrc.py (no network)."""


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

# The LRCLIB cache (naming, TTL, politeness delay) is covered by tests/test_lrc.py.
