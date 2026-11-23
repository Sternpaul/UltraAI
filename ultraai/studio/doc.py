"""The editor's document: an UltraStar song with all times in seconds.

The editor never sees beats, BPM or GAP.  ``song_to_doc`` converts a txt song into a JSON
document; ``doc_to_song`` quantises it back onto the song's beat grid (BPM/GAP kept, so an
untouched song round-trips exactly; generated songs use ~40-50 ms beats).

Document shape::

    {"headers": [["TITLE", "..."], ...], "bpm": 312.5, "gap_ms": 1234.0,
     "voices": [{"voice": 1, "lines": [
         {"notes": [{"start": 1.23, "end": 1.5, "pitch": 4, "text": "Hel", "kind": ":"}, ...],
          "break": 2.1 | null}]}]}

``pitch`` is the txt value (semitones relative to C4); ``text`` keeps the txt spacing
(a trailing space ends a word, "~" continues a syllable).
"""

from __future__ import annotations

from ..ultrastar.model import NOTE_KINDS, Line, Note, Song

BODY_HEADERS = {"BPM", "GAP", "RELATIVE", "ENCODING"}


def song_to_doc(song: Song) -> dict:
    voices = []
    for v in sorted(song.voices):
        lines = []
        for ln in song.voices[v]:
            lines.append({
                "notes": [{"start": round(song.beat_to_seconds(n.start), 4),
                           "end": round(song.beat_to_seconds(n.end), 4),
                           "pitch": n.pitch, "text": n.text, "kind": n.kind} for n in ln.notes],
                "break": round(song.beat_to_seconds(ln.break_beat), 4) if ln.break_beat is not None else None,
            })
        voices.append({"voice": v, "lines": lines})
    return {"headers": [[k, v] for k, v in song.headers if k not in BODY_HEADERS],
            "bpm": song.bpm, "gap_ms": song.gap_ms, "voices": voices}


class DocError(ValueError):
    pass


def doc_to_song(doc: dict) -> Song:
    """Quantise a document onto its beat grid.  Notes keep their order; rounding never
    makes notes overlap (a note is pushed to start at the previous note's end)."""
    bpm = float(doc.get("bpm") or 300.0)
    if bpm <= 0:
        raise DocError("bpm must be positive")
    spb = 60.0 / (4.0 * bpm)
    starts = [n["start"] for v in doc["voices"] for ln in v["lines"] for n in ln["notes"]]
    if not starts:
        raise DocError("song has no notes")
    gap_ms = float(doc.get("gap_ms", 0.0))
    if min(starts) < gap_ms / 1000.0 - spb / 2:  # a note was moved before beat 0: move GAP back
        gap_ms = round(max(0.0, min(starts)) * 1000.0, 2)
    gap_s = gap_ms / 1000.0
    song = Song(headers=[(k, v) for k, v in doc.get("headers", []) if k not in BODY_HEADERS])
    song.set("BPM", _num(bpm))
    song.set("GAP", _num(gap_ms))

    def beat(t: float) -> int:
        return int(round((float(t) - gap_s) / spb))

    for voice in doc["voices"]:
        lines: list[Line] = []
        prev_end, prev_end_s = 0, float("-inf")
        for ln in voice["lines"]:
            notes = []
            for n in sorted(ln["notes"], key=lambda n: n["start"]):
                kind = n.get("kind", ":")
                if kind not in NOTE_KINDS:
                    raise DocError(f"unknown note kind {kind!r}")
                text = str(n.get("text", ""))
                s = beat(n["start"])
                if n["start"] >= prev_end_s - 1e-6:  # only overlaps caused by rounding are fixed
                    s = max(s, prev_end)
                e = max(s + (1 if n["end"] > n["start"] + 1e-6 else 0), beat(n["end"]))
                notes.append(Note(kind, s, e - s, int(n.get("pitch", 0)), text))
                prev_end, prev_end_s = e, float(n["end"])
            if notes:
                lines.append(Line(notes, None if ln.get("break") is None else beat(ln["break"])))
        for k, line in enumerate(lines[:-1]):  # breaks must sit between two lines
            nxt = lines[k + 1].start
            b = line.break_beat if line.break_beat is not None else (line.end + nxt) // 2
            if nxt >= line.end and (b < line.start or b > nxt):  # outside its gap after edits
                b = min(max(line.end, (line.end + nxt) // 2), nxt)
            line.break_beat = b
        song.voices[int(voice.get("voice", 1))] = lines
    return song


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.3f}".rstrip("0").rstrip(".")
