"""Note-generation core of ultraai/generate.py with a synthetic PitchTrack + alignment."""

import pytest

from ultraai import generate as g
from ultraai.lyrics import prepare
from ultraai.tempo import Grid


def test_build_syllables_times_and_pitches(make_aligned, track_factory):
    lyr = prepare(["hello world"], "en")
    words = make_aligned(lyr, [0.0, 0.6])
    words[0].syllable_starts = [0.0, 0.3]                  # "hel" | "lo"
    p = track_factory([(0.0, 0.8, 60)], duration=1.0)       # one held note at midi 60
    syl = g.build_syllables(lyr, words, p)
    assert [s.text for s in syl] == ["hel", "lo ", "world "]
    assert [s.start for s in syl] == pytest.approx([0.0, 0.3, 0.6])
    assert [s.end for s in syl] == pytest.approx([0.3, 0.6, 0.8])   # ends at the next syllable / voice
    assert [s.pitch for s in syl] == [60, 60, 60]
    assert [s.word for s in syl] == [0, 0, 1]
    assert all(s.line == 0 for s in syl)
    assert syl[0].voiced == pytest.approx(1.0)
    assert syl[0].stability == pytest.approx(0.0)


def test_build_syllables_melisma_split(make_aligned, track_factory):
    lyr = prepare(["whoa"], "en")
    words = make_aligned(lyr, [0.0])
    p = track_factory([(0.0, 0.5, 60), (0.5, 1.2, 64)], duration=1.5)  # two stable regions
    syl = g.build_syllables(lyr, words, p)
    assert len(syl) == 1 and syl[0].end == pytest.approx(1.2)
    seg = syl[0].segments
    assert seg is not None and len(seg) == 2
    assert [m for _, _, m in seg] == [60, 64]
    assert seg[0][0] == pytest.approx(0.0)
    assert seg[1][1] == pytest.approx(1.2)


def test_segments_splits_at_the_pitch_change(track_factory):
    p = track_factory([(0.0, 0.5, 60), (0.5, 1.0, 64)], duration=1.0)
    seg = g._segments(p, 0.0, 1.0)
    assert [m for _, _, m in seg] == [60, 64]
    assert seg[0][0] == pytest.approx(0.0) and seg[1][1] == pytest.approx(1.0)
    assert seg[0][1] == pytest.approx(seg[1][0])           # contiguous
    assert seg[0][1] == pytest.approx(0.5, abs=0.02)       # at the pitch change


def test_segments_absorbs_runs_shorter_than_min_seg(track_factory):
    p = track_factory([(0.0, 0.5, 60), (0.5, 1.0, 64)], duration=1.0)
    seg = g._segments(p, 0.0, 1.0, min_seg_s=0.6)          # neither half lasts 0.6 s
    assert len(seg) == 1
    assert seg[0][0] == pytest.approx(0.0) and seg[0][1] == pytest.approx(1.0)


def test_segments_empty_when_unvoiced_or_tiny(track_factory):
    assert g._segments(track_factory([], duration=1.0), 0.0, 1.0) == []
    p = track_factory([(0.0, 1.0, 60)], duration=1.0)
    assert g._segments(p, 0.0, 0.02) == []


def _long_line(n=6):
    """One lyric line of n words, with a single 1 s pause before word 3."""
    syl = []
    for wi in range(n):
        start = wi * 0.5 + (1.0 if wi >= 3 else 0.0)
        s = g.Syllable(f"word{wi}xyz ", start, start + 0.4, pitch=60, word=wi, line=0)
        s.voiced, s.stability, s.score = 0.9, 0.2, 0.9
        syl.append(s)
    return syl


def test_split_lines_at_the_biggest_pause():
    syl = _long_line()                                     # 54 chars > maxc 40 -> must split
    g.split_lines(syl)
    assert [s.line for s in syl] == [0, 0, 0, 1, 1, 1]      # split at the 1 s pause


def test_split_lines_keeps_short_lines():
    syl = _long_line(2)
    g.split_lines(syl)
    assert [s.line for s in syl] == [0, 0]


def test_drop_unsung_vocables():
    sung = [g.Syllable("hel", 0.0, 0.3, pitch=60, word=0, line=0),
            g.Syllable("lo ", 0.3, 0.6, pitch=60, word=0, line=0)]
    voc = [g.Syllable("ah ", 2.0, 2.5, word=1, line=1),
           g.Syllable("ah ", 2.5, 3.0, word=1, line=1)]    # "ah ah" where nobody sings
    for s in sung:
        s.voiced, s.score = 0.9, 0.9
    for s in voc:
        s.voiced, s.score = 0.05, 0.9
    kept, n = g.drop_unsung_vocables(sung + voc)
    assert n == 1
    assert [s.text for s in kept] == ["hel", "lo "]


def _syl():
    syl = [g.Syllable("hel", 0.0, 0.3, pitch=60, word=0, line=0),
           g.Syllable("lo ", 0.3, 0.6, pitch=62, word=0, line=0),
           g.Syllable("world ", 1.0, 1.4, pitch=64, word=1, line=1)]
    for s in syl:
        s.voiced, s.stability, s.score = 0.9, 0.2, 0.9
    return syl


def test_to_song_headers_and_beat_quantisation():
    song = g.to_song(_syl(), {"TITLE": "T", "ARTIST": "A"}, "song.mp3")
    assert song.get("TITLE") == "T" and song.get("ARTIST") == "A"
    assert song.get("BPM") == "300"                        # fallback grid: 50 ms beats
    assert song.get("GAP") == "0"
    assert song.get("MP3") == "song.mp3" and song.get("AUDIO") == "song.mp3"
    assert song.get("CREATOR") == "UltraAI"
    lines = song.voices[1]
    assert len(lines) == 2
    notes = [n for ln in lines for n in ln.notes]
    # beats = round(t / 0.05): 0.3 s -> 6, 0.6 s -> 12, 1.0 s -> 20, 1.4 s -> 28
    assert [(n.start, n.end) for n in notes] == [(0, 5), (6, 12), (20, 28)]
    assert [n.pitch for n in notes] == [0, 2, 4]           # midi - 60
    assert [n.text for n in notes] == ["hel", "lo ", "world "]
    assert all(n.kind == ":" for n in notes)
    for a, b in zip(notes, notes[1:]):
        assert b.start >= a.end                            # no overlaps
    assert lines[0].break_beat == 16                       # 8-beat gap -> break placed inside it
    assert lines[1].break_beat is None


def test_to_song_with_tempo_grid():
    song = g.to_song(_syl(), {}, "x.mp3", Grid(240.0, 1.0, 120.0))  # 62.5 ms beats, beat 0 at 1 s
    assert song.get("BPM") == "240" and song.get("GAP") == "1000"
    notes = [n for ln in song.voices[1] for n in ln.notes]
    assert [(n.start, n.end) for n in notes] == [(0, 1), (1, 2), (2, 6)]


def test_to_song_melisma_continuation_notes():
    s = g.Syllable("whoa ", 0.0, 1.0, pitch=60, word=0, line=0,
                   segments=[(0.0, 0.5, 60), (0.5, 1.0, 64)])
    s.voiced, s.stability, s.score = 0.9, 0.2, 0.9
    song = g.to_song([s], {}, "x.mp3")
    notes = song.voices[1][0].notes
    assert len(notes) == 2
    assert notes[0].text == "whoa" and notes[0].pitch == 0
    assert notes[1].is_continuation and notes[1].text.startswith("~")
    assert notes[1].pitch == 4
    assert notes[1].start == notes[0].end == 10            # melisma notes stay connected
