"""Song-level check: a warning when the lyrics were barely heard or most lines are uncertain."""

from ultraai.issues import song_check


def _info(heard=None, low=0, lines=10):
    return {"heard": heard, "low_confidence_lines": low, "lines": [{} for _ in range(lines)]}


def test_good_song_has_no_check():
    assert song_check(_info(heard=0.9, low=2)) is None


def test_lyrics_barely_heard():
    i = song_check(_info(heard=0.25, low=1), start=12.0)
    assert i["kind"] == "song_check" and i["text"] == "Lyrics may not match the singing"
    assert i["start"] == 12.0 and i["risk"] > 0.9
    assert "25 %" in i["reasons"][0]


def test_many_uncertain_lines():
    i = song_check(_info(heard=0.8, low=6))
    assert i["text"] == "Check this song carefully" and "60 %" in i["reasons"][0]


def test_both_reasons_and_no_heard_value():
    assert len(song_check(_info(heard=0.1, low=9))["reasons"]) == 2
    assert song_check(_info(heard=None, low=1)) is None  # m2 path: no transcript comparison
    assert song_check(_info(heard=None, low=5))["text"] == "Check this song carefully"
