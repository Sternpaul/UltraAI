from pathlib import Path

import pytest

from ultraai.ultrastar.model import Note
from ultraai.ultrastar.reader import read, read_bytes
from ultraai.ultrastar.validator import validate
from ultraai.ultrastar.writer import dumps, upgrade_to_v1

SONGS = sorted(Path(__file__).resolve().parent.parent.glob("songs/*/*.txt"))

SAMPLE = (
    "#TITLE:Test\r\n#ARTIST:Me\r\n#MP3:a.mp3\r\n#BPM:300,5\r\n#GAP:1000\r\n"
    ": 0 4 0 Hel\r\n: 4 4 2 lo \r\n* 8 2 4 world\r\n: 10 2 5 ~\r\n- 14\r\n"
    ": 16 4 0  next\r\nF 20 4 0  line\r\nE\r\nignored garbage\r\n"
)


def test_parse_sample():
    s = read_bytes(SAMPLE.encode())
    assert s.bpm == 300.5 and s.gap_ms == 1000
    lines = s.lines()
    assert len(lines) == 2 and lines[0].break_beat == 14
    assert lines[0].notes[1].text == "lo "          # trailing space kept
    assert lines[1].notes[0].text == " next"        # only ONE separator consumed
    assert lines[0].text == "Hello world"
    assert [w.text for w in s.words()] == ["Hello", "world", "next", "line"]
    assert len(s.words()[1].syllables) == 1          # '~' is not a new syllable
    assert s.beat_to_seconds(0) == pytest.approx(1.0)
    assert s.beat_to_seconds(4) == pytest.approx(1.0 + 4 * 60 / (4 * 300.5))


def test_relative_mode():
    txt = "#TITLE:T\n#ARTIST:A\n#MP3:a\n#BPM:300\n#RELATIVE:yes\n: 0 2 0 a\n- 4 10\n: 0 2 0 b\nE\n"
    s = read_bytes(txt.encode())
    assert s.was_relative
    assert s.lines()[1].notes[0].start == 10
    assert "RELATIVE" not in dumps(s)


def test_duet_roundtrip():
    txt = "#TITLE:T\n#ARTIST:A\n#MP3:a\n#BPM:300\n#P1:X\n#P2:Y\nP1\n: 0 2 0 a\nP2\n: 4 2 0 b\nE\n"
    s = read_bytes(txt.encode())
    assert s.is_duet
    again = read_bytes(dumps(s).encode())
    assert again.voices.keys() == s.voices.keys()


def test_cp1252_fallback():
    s = read_bytes("#TITLE:Gr\xf6nemeyer\n#ARTIST:A\n#MP3:a\n#BPM:1\n: 0 1 0 \xe4\nE\n".encode("cp1252"))
    assert s.encoding == "cp1252" and s.title == "Grönemeyer"


def test_upgrade_to_v1():
    s = read_bytes(b"#TITLE:T\n#ARTIST:A\n#MP3:a.mp3\n#BPM:1\n#ENCODING:CP1252\n#DUETSINGERP1:X\n: 0 1 0 a\nE\n")
    v1 = upgrade_to_v1(s)
    assert v1.get("VERSION") == "1.0.0" and v1.get("AUDIO") == "a.mp3" and v1.get("P1") == "X"
    assert not v1.has("ENCODING") and not v1.has("DUETSINGERP1")


def test_validator_flags_problems():
    s = read_bytes(b"#VERSION:2.0.0\n#TITLE:T\n#ARTIST:A\n#BPM:300\n: 0 4 0 a\n: 2 4 0 b\n- 3\n: 10 1 0 c\nE\n")
    codes = {(i.level, i.code) for i in validate(s)}
    assert ("error", "version") in codes and ("error", "header") in codes
    assert ("warning", "overlap") in codes and ("warning", "break") in codes


def _signature(song):
    return {v: [([(n.kind, n.start, n.length, n.pitch, n.text) for n in ln.notes], ln.break_beat)
                 for ln in lines] for v, lines in song.voices.items()}


@pytest.mark.skipif(not SONGS, reason="no songs/ library present")
@pytest.mark.parametrize("path", SONGS, ids=lambda p: p.parent.name)
def test_library_roundtrip(path):
    song = read(path)
    again = read_bytes(dumps(song).encode("utf-8"))
    assert _signature(again) == _signature(song)
    assert dict(again.headers) == {k: v for k, v in song.headers if k not in ("ENCODING", "RELATIVE")}
    assert not [i for i in validate(song, path.parent) if i.level == "error"]


# ---- metrics ---------------------------------------------------------------
import copy  # noqa: E402

from ultraai.eval.metrics import compare  # noqa: E402


def test_metrics_identity_and_shift():
    s = read_bytes(SAMPLE.encode())
    m = compare(s, s)
    assert m["word_recall"] == 1 and m["onset_median_ms"] == 0 and m["line_f1"] == 1
    assert m["pitch_class_acc"] == 1 and m["perfect_singer_score"] == 1
    shifted = copy.deepcopy(s)
    shifted.set("GAP", "1100")
    m = compare(s, shifted)
    assert m["onset_median_ms"] == pytest.approx(100) and m["onset_bias_ms"] == pytest.approx(100)
    transposed = copy.deepcopy(s)
    for n in transposed.notes():
        n.pitch += 12
    m = compare(s, transposed)
    assert m["pitch_class_acc"] == 1 and m["octave_offset"] == 1


def test_usdb_text_cover_link():
    from ultraai.studio.store import usdb_text, youtube_cover_url

    assert youtube_cover_url("UbstNV2FUNY") == "https://img.youtube.com/vi/UbstNV2FUNY/hqdefault.jpg"
    url = "https://www.youtube.com/watch?v=UbstNV2FUNY"
    txt = SAMPLE.replace("#MP3:a.mp3", "#MP3:a.mp3\n#COVER:A - B [CO].jpg\n#AUDIOURL:https://www.youtube.com/watch?v=UbstNV2FUNY")

    # YouTube-sourced song: the cover link is a co= meta tag inside #VIDEO (USDB Syncer
    # convention), pointing at the video's thumbnail; the local file name is dropped
    u = usdb_text(txt, url)
    assert "#VIDEO:v=UbstNV2FUNY,co=https://img.youtube.com/vi/UbstNV2FUNY/hqdefault.jpg" in u
    assert "#COVER:" not in u and "#COVERURL" not in u and "#AUDIOURL" not in u and "#MP3:a.mp3" in u

    # an already-linked cover (co= or #COVERURL) wins over the derived thumbnail; other meta tags survive
    u = usdb_text(txt.replace("#AUDIOURL:", "#COVERURL:https://example.invalid/c.jpg\n#AUDIOURL:"), url)
    assert "#VIDEO:v=UbstNV2FUNY,co=https://example.invalid/c.jpg" in u
    assert "img.youtube.com" not in u and "#COVER:" not in u and "#COVERURL" not in u
    u = usdb_text(SAMPLE.replace("#MP3:a.mp3", "#MP3:a.mp3\n#VIDEO:v=aaaaaaaaaaa,preview=12.5,medley=0-99"), url)
    assert "#VIDEO:v=UbstNV2FUNY,preview=12.5,medley=0-99,co=https://img.youtube.com/vi/UbstNV2FUNY/hqdefault.jpg" in u

    # no YouTube link and no cover link: the local cover stays, nothing is linked
    u = usdb_text(SAMPLE.replace("#MP3:a.mp3", "#MP3:a.mp3\n#COVER:A - B [CO].jpg"), "some local file.mp3")
    assert "co=" not in u and "#COVERURL" not in u and "#COVER:A - B [CO].jpg" in u and "#VIDEO:" not in u
