"""Measure the conventions hand-made songs follow, to calibrate our generator.

Usage: python scripts/survey_library.py [songs_dir]
"""

import statistics
import sys
from collections import Counter
from pathlib import Path

from ultraai.ultrastar.reader import read


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def describe(name, values, unit=""):
    print(f"  {name:34} median {statistics.median(values):7.2f}{unit}   p10 {pct(values, .1):7.2f}"
          f"   p90 {pct(values, .9):7.2f}   max {max(values):7.2f}")


def main(root="songs"):
    kinds, langs = Counter(), Counter()
    lead = trail = 0
    tilde_notes = notes_total = 0
    line_chars, line_words, line_secs, words_per_song = [], [], [], []
    note_ms, gap_beats, gap_ms, break_pos, break_gap_s = [], [], [], [], []
    syl_per_word, notes_per_syl, beat_ms, golden_frac = [], [], [], []
    pitch_range = []
    for txt in sorted(Path(root).glob("*/*.txt")):
        s = read(txt)
        langs[s.get("LANGUAGE", "?")] += 1
        spb = s.seconds_per_beat
        beat_ms.append(spb * 1000)
        ns = list(s.notes())
        notes_total += len(ns)
        golden_frac.append(sum(n.is_golden for n in ns) / len(ns))
        ps = [n.pitch for n in ns if n.is_pitched]
        pitch_range.append(max(ps) - min(ps))
        for n in ns:
            kinds[n.kind] += 1
            note_ms.append(n.length * spb * 1000)
            if n.is_continuation:
                tilde_notes += 1
            elif n.text[:1] == " ":
                lead += 1
            elif n.text[-1:] == " ":
                trail += 1
        for a, b in zip(ns, ns[1:]):
            if b.start >= a.end:
                gap_beats.append(b.start - a.end)
                gap_ms.append((b.start - a.end) * spb * 1000)
        lines = s.lines()
        for li, line in enumerate(lines):
            line_chars.append(len(line.text))
            line_words.append(len(line.text.split()))
            line_secs.append((line.end - line.start) * spb)
            if li + 1 < len(lines) and line.break_beat is not None:
                gap = lines[li + 1].start - line.end
                if gap > 0:
                    break_pos.append((line.break_beat - line.end) / gap)
                    break_gap_s.append(gap * spb)
        words = s.words()
        words_per_song.append(len(words))
        for w in words:
            syl_per_word.append(len(w.syllables))
            for k, syl in enumerate(w.syllables):
                pass
        cont = sum(n.is_continuation for n in ns)
        notes_per_syl.append(len(ns) / max(1, len(ns) - cont))

    print(f"songs: {len(beat_ms)}   notes: {notes_total}")
    print("languages:", dict(langs))
    print("note kinds:", dict(kinds))
    print(f"word separator: leading space {lead}, trailing space {trail};  '~' notes {tilde_notes} "
          f"({tilde_notes / notes_total:.1%} of notes)")
    describe("beat length (ms)", beat_ms)
    describe("note length (ms)", note_ms)
    describe("gap between notes (beats)", gap_beats)
    describe("gap between notes (ms)", gap_ms)
    describe("line length (chars)", line_chars)
    describe("line length (words)", line_words)
    describe("line duration (s)", line_secs)
    describe("break position in gap (0=end,1=next)", break_pos)
    describe("syllables per word", syl_per_word)
    describe("notes per syllable (song avg)", notes_per_syl)
    describe("golden note fraction", golden_frac)
    describe("pitch range (semitones)", pitch_range)
    print(f"  zero-gap note pairs: {sum(g == 0 for g in gap_beats) / len(gap_beats):.1%}")
    near = [p for p, g in zip(break_pos, break_gap_s) if g < 1.0]
    far = [p for p, g in zip(break_pos, break_gap_s) if g >= 2.0]
    if near:
        describe("break position, gap < 1 s", near)
    if far:
        describe("break position, gap >= 2 s", far)
        far_s = [p * g for p, g in zip(break_pos, break_gap_s) if g >= 2.0]
        describe("  seconds after line end (gap>=2s)", far_s, "s")


if __name__ == "__main__":
    main(*sys.argv[1:])
