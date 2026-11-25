"""Song by-products (B5a): word-synced .lrc, karaoke .ass, #PREVIEWSTART = first chorus."""

import re

import pytest

from ultraai.byproducts import ass_text, lrc_text, write_byproducts
from ultraai.generate import Syllable, to_song
from ultraai.tempo import Grid
from ultraai.ultrastar.model import Line, Note, Song
from ultraai.ultrastar.reader import read_bytes
from ultraai.ultrastar.writer import dumps, write

# no conftest on this branch: local helpers only

LRC_RE = re.compile(r"\[(\d+):(\d{2})\.(\d{2})\]")
KF_RE = re.compile(r"\\kf(\d+)")
ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")


def make_song(bpm: str = "300", gap: str = "1000") -> Song:
    """Synthetic song: BPM 300 -> one beat = 0.05 s, GAP 1000 ms -> beat 0 at 1.00 s."""
    song = Song(headers=[("VERSION", "1.0.0"), ("TITLE", "T"), ("ARTIST", "A"), ("MP3", "a.mp3"),
                         ("BPM", bpm), ("GAP", gap)])
    song.voices[1] = [
        Line(notes=[Note(":", 0, 4, 0, "Hel"), Note(":", 4, 4, 2, "lo "), Note("*", 8, 2, 4, "world")],
             break_beat=12),
        Line(notes=[Note(":", 16, 4, 0, "next"), Note("F", 20, 4, 0, " line")]),
    ]
    return song


def tag_cs(m: int, s: int, c: int) -> int:
    return m * 6000 + s * 100 + c


def ass_time_cs(text: str) -> int:
    h, m, s, c = ASS_TIME_RE.match(text).groups()
    return int(h) * 360000 + int(m) * 6000 + int(s) * 100 + int(c)


# ---- .lrc ---------------------------------------------------------------------

def test_lrc_one_tag_per_note_grouped_per_line():
    song = make_song()
    out = [ln for ln in lrc_text(song).splitlines() if ln]
    assert len(out) == len(song.lines())                      # tags grouped per screen line
    assert out[0] == "[00:01.00]Hel[00:01.20]lo [00:01.40]world"
    assert out[1] == "[00:01.80]next[00:02.00] line"


@pytest.mark.parametrize("bpm,gap", [("300", "1000"), ("273.4", "1234"), ("120", "0")])
def test_lrc_times_round_trip_at_centisecond_precision(bpm, gap):
    song = make_song(bpm, gap)
    tags = [t for ln in lrc_text(song).splitlines() for t in LRC_RE.findall(ln)]
    expected = [round(song.beat_to_seconds(n.start) * 100) for n in song.notes()]
    got = [tag_cs(int(m), int(s), int(c)) for m, s, c in tags]
    assert got == expected                                     # parse back -> same centiseconds


# ---- .ass ---------------------------------------------------------------------

def test_ass_structure_and_kf_durations():
    song = make_song()
    text = ass_text(song)
    assert "[Script Info]" in text and "[V4+ Styles]" in text and "[Events]" in text
    assert "ScriptType: v4.00+" in text
    dlg = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    assert len(dlg) == len(song.lines())                       # one Dialogue per song line
    for ln, line in zip(dlg, song.lines()):
        parts = ln.split(",", 9)
        assert parts[0] == "Dialogue: 0"
        assert parts[3] == "Default"
        assert ass_time_cs(parts[1]) == round(song.beat_to_seconds(line.start) * 100)
        assert ass_time_cs(parts[2]) == round(song.beat_to_seconds(line.end) * 100)
        kfs = [int(x) for x in KF_RE.findall(parts[9])]
        exp = [round((song.beat_to_seconds(n.end) - song.beat_to_seconds(n.start)) * 100)
               for n in line.notes]
        assert kfs == exp                                      # \kf sized by note duration (cs)
    assert "{\\kf20}Hel{\\kf20}lo {\\kf10}world" in dlg[0]


# ---- #PREVIEWSTART ------------------------------------------------------------

def _syl(text, start, end, line, word):
    return Syllable(text, start, end, pitch=60, word=word, line=line, score=1.0, voiced=1.0)


def _song_with_chorus() -> Song:
    """Two unique verses around a twice-sung chorus line (screen lines 1 and 3)."""
    syl = [
        _syl("verse ", 0.5, 0.9, line=0, word=0), _syl("alpha ", 1.0, 1.4, line=0, word=1),
        _syl("chorus ", 2.0, 2.4, line=1, word=2), _syl("beta ", 2.5, 2.9, line=1, word=3),
        _syl("verse ", 3.0, 3.4, line=2, word=4), _syl("gamma ", 3.5, 3.9, line=2, word=5),
        _syl("chorus ", 4.0, 4.4, line=3, word=6), _syl("beta ", 4.5, 4.9, line=3, word=7),
    ]
    return to_song(syl, {"TITLE": "T", "ARTIST": "A"}, "a.mp3", grid=Grid(300.0, 1.0, None))


def test_previewstart_is_first_chorus_start():
    song = _song_with_chorus()
    # chorus first sung at 2.0 s -> beat 20 (GAP 1 s, 0.05 s/beat) -> back to 2.00 s
    assert song.get("PREVIEWSTART") == "2.00"
    assert "#PREVIEWSTART:2.00" in dumps(song)


def test_previewstart_absent_without_repeats(monkeypatch):
    monkeypatch.delenv("ULTRAAI_PREVIEWSTART", raising=False)
    syl = [_syl("only ", 0.5, 0.9, line=0, word=0), _syl("unique ", 1.0, 1.4, line=1, word=1),
           _syl("lines ", 2.0, 2.4, line=2, word=2)]
    song = to_song(syl, {"TITLE": "T"}, "a.mp3", grid=Grid(300.0, 1.0, None))
    assert song.get("PREVIEWSTART") is None


def test_previewstart_absent_when_config_off(monkeypatch):
    monkeypatch.setenv("ULTRAAI_PREVIEWSTART", "0")
    assert _song_with_chorus().get("PREVIEWSTART") is None


def test_previewstart_writer_round_trip():
    song = _song_with_chorus()
    again = read_bytes(dumps(song).encode())
    assert again.get("PREVIEWSTART") == song.get("PREVIEWSTART") == "2.00"


# ---- write_byproducts ----------------------------------------------------------

def test_write_byproducts_writes_both_sidecars(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_EXPORT_LRC", "1")
    monkeypatch.setenv("ULTRAAI_EXPORT_ASS", "1")
    song = make_song()
    txt = tmp_path / "song.txt"
    write(song, txt)
    written = write_byproducts(song, txt)
    assert {p.name for p in written} == {"song.lrc", "song.ass"}
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == lrc_text(song)
    assert (tmp_path / "song.ass").read_text(encoding="utf-8") == ass_text(song)
    assert not list(tmp_path.glob("*.tmp"))                   # atomic write leaves no tmp


def test_write_byproducts_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_EXPORT_LRC", "0")
    monkeypatch.setenv("ULTRAAI_EXPORT_ASS", "0")
    song = make_song()
    txt = tmp_path / "song.txt"
    write(song, txt)
    assert write_byproducts(song, txt) == []
    assert not (tmp_path / "song.lrc").exists()
    assert not (tmp_path / "song.ass").exists()


def test_write_byproducts_respects_each_option(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_EXPORT_LRC", "0")
    monkeypatch.setenv("ULTRAAI_EXPORT_ASS", "1")
    txt = tmp_path / "song.txt"
    write(make_song(), txt)
    assert [p.name for p in write_byproducts(make_song(), txt)] == ["song.ass"]
    assert not (tmp_path / "song.lrc").exists() and (tmp_path / "song.ass").exists()
