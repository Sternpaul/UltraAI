import glob
import json
from pathlib import Path

import pytest

from ultraai.studio.doc import doc_to_song, song_to_doc
from ultraai.ultrastar.reader import read

FILES = sorted(glob.glob("songs/*/*.txt"))[:10]


def _body(song):
    return [(v, [(n.kind, n.start, n.length, n.pitch, n.text) for n in ln.notes], ln.break_beat)
            for v in song.voices for ln in song.voices[v]]


@pytest.mark.parametrize("path", FILES)
def test_round_trip_is_exact(path):
    s = read(Path(path))
    t = doc_to_song(json.loads(json.dumps(song_to_doc(s))))
    assert _body(t) == _body(s)
    assert abs(t.bpm - s.bpm) < 1e-9 and abs(t.gap_ms - s.gap_ms) < 0.01


def test_edits_are_quantised_and_kept_consistent():
    doc = {"headers": [["TITLE", "T"], ["ARTIST", "A"]], "bpm": 300, "gap_ms": 1000, "voices": [{"voice": 1, "lines": [
        {"notes": [{"start": 1.0, "end": 1.2, "pitch": 0, "text": "a ", "kind": ":"},
                   {"start": 1.21, "end": 1.4, "pitch": 2, "text": "b ", "kind": "*"}], "break": 9.0},
        {"notes": [{"start": 0.5, "end": 0.8, "pitch": 0, "text": "c ", "kind": ":"}], "break": None}]}]}
    # second line dragged before the GAP and before line 1: GAP moves, break re-centred later
    doc["voices"][0]["lines"][1]["notes"][0].update(start=2.0, end=2.3)
    doc["voices"][0]["lines"][0]["notes"][0].update(start=0.9)
    s = doc_to_song(doc)
    assert s.gap_ms == 900.0
    a, b = s.voices[1]
    assert a.notes[0].start == 0 and a.notes[1].start >= a.notes[0].end
    assert a.end <= a.break_beat <= b.start
