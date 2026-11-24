"""M1 baseline generator: audio + lyrics -> UltraStar Song.

Steps: separate vocals -> forced-align lyrics (char level) -> pitch track ->
one note per syllable (start from alignment, end from voicing) -> fixed 50 ms beat
grid -> screen lines from the lyric lines -> '-' placement learnt from the library.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import align as aligner
from . import config
from .audio import DEFAULT_SEPARATION, load_mono, separate
from .lyrics import Lyrics
from .onsets import onsets_for, snap_words_to_onsets
from .pitch import PitchTrack, track
from .pitchfit import fit_syllables
from .ultrastar.model import Line, Note, Song, format_number
from .video import apply_videogap

log = logging.getLogger(__name__)

BEAT_S = 0.05             # fixed grid for M1: 50 ms per beat (#BPM 300)
MAX_VOICED_GAP_S = 0.08   # unvoiced gaps (consonants) bridged inside a note
MIN_NOTE_S = 0.08

# per-stage seconds of the pipeline, accumulated by stage() when ULTRAAI_STAGE_TIMING=1
# (benchmarks only; default off, so generated .txt output is never affected)
STAGE_TIMES: dict[str, float] = {}


def reset_stage_times() -> None:
    """Clear STAGE_TIMES (benchmarks call this before each run)."""
    STAGE_TIMES.clear()


@contextlib.contextmanager
def stage(name: str):
    """Time the block into ``STAGE_TIMES[name]`` (accumulating) when stage timing is on."""
    if not config.get_bool("stage_timing"):
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        STAGE_TIMES[name] = STAGE_TIMES.get(name, 0.0) + (time.perf_counter() - t0)


@dataclass
class Syllable:
    text: str            # display text incl. trailing space at word end
    start: float
    end: float = 0.0
    pitch: int = 0
    word: int = 0
    line: int = 0
    score: float = 1.0
    segments: list[tuple[float, float, int]] | None = None  # melisma: (start, end, midi) per note
    voiced: float = 1.0          # share of voiced frames
    stability: float = 0.0       # pitch spread (semitones, IQR) inside the syllable
    kind: str = ":"
    src_line: int = 0            # lyric line given to the aligner (``line`` becomes the screen line)
    auto_fixed: bool = False     # pitch auto-corrected by the double-check consensus (B3)


def _note_end(p: PitchTrack, start: float, limit: float) -> float:
    """Extend from ``start`` while the voice keeps sounding (bridging short gaps)."""
    voiced = p.voiced()
    i, stop = p.index(start), p.index(limit)
    last_voiced = None
    gap = 0.0
    while i < stop:
        if voiced[i]:
            last_voiced, gap = i, 0.0
        elif last_voiced is not None:
            gap += p.frame
            if gap > MAX_VOICED_GAP_S:
                break
        elif p.times[i] - start > 0.25:  # never found voice near the onset
            break
        i += 1
    if last_voiced is None:
        return min(limit, start + MIN_NOTE_S * 2)
    return min(limit, float(p.times[last_voiced]) + p.frame / 2)


def _pitch(p: PitchTrack, start: float, end: float, fallback: int) -> int:
    a, b = p.index(start), max(p.index(end), p.index(start) + 1)
    seg = p.midi[a:b][p.voiced()[a:b]]
    if len(seg) == 0:
        return fallback
    return int(round(float(np.median(seg))))


MIN_MELISMA_SYL_S = 0.3


def note_style() -> tuple[float, int] | None:
    """(min note length within a syllable, min pitch step between its notes) per ULTRAAI_NOTES."""
    name = config.get("notes")
    if name == "easiest":                            # one note per syllable
        return None
    if name == "easier":                              # only clear, held changes become extra notes
        return (0.3, 3)
    return (config.get_float("minseg"), 1)            # "singer": follow the voice


def _segments(p: PitchTrack, start: float, end: float, min_seg_s: float | None = None,
              min_step: int = 1) -> list[tuple[float, float, int]]:
    """Split [start, end) into stable-pitch regions (semitone runs >= ``min_seg_s``)."""
    from scipy.ndimage import median_filter

    a, b = p.index(start), p.index(end)
    if b - a < 3:
        return []
    midi = p.midi[a:b].copy()
    voiced = p.voiced()[a:b]
    if voiced.sum() < 3:
        return []
    # fill unvoiced frames with the previous voiced pitch, then smooth
    last = np.nan
    for i in range(len(midi)):
        if voiced[i]:
            last = midi[i]
        else:
            midi[i] = last
    first_valid = np.argmax(~np.isnan(midi))
    midi[:first_valid] = midi[first_valid]
    # undo octave jumps relative to the syllable median
    med = np.median(midi[voiced])
    midi = midi - 12 * np.round((midi - med) / 12)
    semis = np.round(median_filter(midi, size=5, mode="nearest")).astype(int)
    runs: list[list[int]] = []  # [pitch, first, last]
    for i, v in enumerate(semis):
        if runs and runs[-1][0] == v:
            runs[-1][2] = i
        else:
            runs.append([v, i, i])
    min_frames = max(1, int((min_seg_s if min_seg_s is not None else config.get_float("minseg")) / p.frame))
    # absorb short runs into the longer neighbour until all are long enough and, for long
    # wobbly notes ("whoooah"), until at most MAX_SEGMENTS remain (the main steps survive)
    while len(runs) > 1:
        lens = [r[2] - r[1] + 1 for r in runs]
        k = int(np.argmin(lens))
        if lens[k] >= min_frames and len(runs) <= max(config.get_int("maxseg"), int((end - start) / 0.35)):
            break
        if k == 0:
            j = 1
        elif k == len(runs) - 1:
            j = k - 1
        else:
            j = k - 1 if lens[k - 1] >= lens[k + 1] else k + 1
        lo, hi = min(runs[k][1], runs[j][1]), max(runs[k][2], runs[j][2])
        runs[j][1], runs[j][2] = lo, hi
        del runs[k]
        merged = []  # re-merge equal neighbours
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    while min_step > 1 and len(runs) > 1:  # easier songs: small steps are not worth a note
        diffs = [abs(runs[k + 1][0] - runs[k][0]) for k in range(len(runs) - 1)]
        k = int(np.argmin(diffs))
        if diffs[k] >= min_step:
            break
        a_, b_ = runs[k], runs[k + 1]
        keep = a_ if (a_[2] - a_[1]) >= (b_[2] - b_[1]) else b_
        runs[k:k + 2] = [[keep[0], a_[1], b_[2]]]
    out = []
    for k, (pitch, i0, i1) in enumerate(runs):
        t0 = start if k == 0 else float(p.times[a + i0])
        t1 = end if k == len(runs) - 1 else float(p.times[a + i1 + 1]) if a + i1 + 1 < len(p.times) else end
        out.append((t0, t1, int(pitch)))
    return out


def build_syllables(lyrics: Lyrics, words: list[aligner.AlignedWord], p: PitchTrack) -> list[Syllable]:
    syl: list[Syllable] = []
    for aw in words:
        w = lyrics.words[aw.index]
        for k, (text, t) in enumerate(zip(w.syllables, aw.syllable_starts)):
            last = k == len(w.syllables) - 1
            syl.append(Syllable(text + (" " if last else ""), t, word=aw.index, line=w.line,
                                score=aw.score, src_line=w.line))
    for k, s in enumerate(syl):
        limit = syl[k + 1].start if k + 1 < len(syl) else s.start + 3.0
        s.end = max(s.start + MIN_NOTE_S, _note_end(p, s.start, limit))
        if k + 1 < len(syl):
            s.end = min(s.end, syl[k + 1].start)
    prev = 60
    vmask = p.voiced()
    for s in syl:
        a, b = p.index(s.start), max(p.index(s.end), p.index(s.start) + 1)
        s.voiced = float(vmask[a:b].mean())
        seg = p.midi[a:b][vmask[a:b]]
        s.stability = float(np.subtract(*np.percentile(seg, [75, 25]))) if len(seg) >= 4 else 99.0
        s.pitch = prev = _pitch(p, s.start, s.end, prev)
        style = note_style()
        if config.get_bool("melisma") and style is not None and s.end - s.start >= MIN_MELISMA_SYL_S:
            seg = _segments(p, s.start, s.end, *style)
            if len(seg) > 1:
                s.segments = seg
    return syl


GOLDEN_SHARE = 0.04       # library: median 4 % of notes
GOLDEN_MIN_S = 0.3
FREESTYLE_MAX_VOICED = 0.1
FREESTYLE_MIN_S = 0.3


_VOCABLES = {"ah", "aah", "oh", "ooh", "oo", "uh", "la", "na", "da", "yeah", "hey", "mm", "mmm", "hmm",
             "whoa", "woah", "wo", "ho", "ha", "eh", "ay", "ayy", "o", "a", "e", "uoh", "ohh", "ahh"}
LINE_MIN_VOICED = 0.25


def drop_unsung_vocables(syl: list[Syllable]) -> tuple[list[Syllable], int]:
    """Remove lines of pure vocables ("ah ah ah") that landed where nobody sings."""
    import re as _re

    by_line: dict[int, list[Syllable]] = {}
    for s in syl:
        by_line.setdefault(s.line, []).append(s)
    drop = set()
    for li, ss in by_line.items():
        words = {_re.sub(r"[^\w]", "", w).lower() for w in "".join(x.text for x in ss).split()} - {""}
        dur = sum(x.end - x.start for x in ss)
        voiced = sum(x.voiced * (x.end - x.start) for x in ss) / max(dur, 1e-6)
        if words and words <= _VOCABLES and voiced < LINE_MIN_VOICED:
            drop.add(li)
    return [s for s in syl if s.line not in drop], len(drop)


def line_report(syl: list[Syllable]) -> list[dict]:
    """Per screen line: time span, alignment confidence, voicing, and whether to review it."""
    by_line: dict[int, list[Syllable]] = {}
    for s in syl:
        by_line.setdefault(s.line, []).append(s)
    out = []
    for li in sorted(by_line):
        ss = by_line[li]
        conf = float(np.mean([x.score for x in ss]))
        voiced = float(np.mean([x.voiced for x in ss]))
        out.append({"start": round(ss[0].start, 3), "end": round(ss[-1].end, 3),
                    "text": "".join(x.text for x in ss).strip(), "confidence": round(conf, 3),
                    "voiced": round(voiced, 3), "review": bool(conf < 0.03 or voiced < 0.3)})
    return out


def low_confidence_lines(syl: list[Syllable]) -> int:
    return sum(r["review"] for r in line_report(syl))


def _line_groups(syl: list[Syllable]) -> list[list[list[Syllable]]]:
    """Screen lines with the same words and syllable count (a repeated chorus line, "whoa"s)."""
    import re as _re

    by_line: dict[int, list[Syllable]] = {}
    for s in syl:
        by_line.setdefault(s.line, []).append(s)
    groups: dict[tuple, list[list[Syllable]]] = {}
    for ss in by_line.values():
        words = _re.sub(r"[^\w\s]", "", "".join(x.text for x in ss).lower()).split()
        if words:
            groups.setdefault((" ".join(words), len(ss)), []).append(ss)
    return [g for g in groups.values() if len(g) > 1]


def _fold(p: float, ref: float) -> float:
    """``p`` moved by whole octaves next to ``ref`` (pitch trackers jump octaves)."""
    while p - ref > 6:
        p -= 12
    while ref - p > 6:
        p += 12
    return p


def _pooled_pitch(p: PitchTrack, spans: list[tuple[float, float]]) -> int | None:
    """Most common semitone over the voiced frames of all spans (octave-folded together)."""
    frames = []
    vm = p.voiced()
    for a_, b_ in spans:
        a, b = p.index(a_), max(p.index(b_), p.index(a_) + 1)
        frames.append(p.midi[a:b][vm[a:b]])
    allf = np.concatenate(frames) if frames else np.array([])
    if len(allf) < 3:
        return None
    ref = float(np.median(allf))
    folded = allf - 12 * np.round((allf - ref) / 12)
    vals, counts = np.unique(np.round(folded).astype(int), return_counts=True)
    return int(vals[np.argmax(counts)])


def harmonize_repeats(syl: list[Syllable], proposals: list | None = None, pitch: PitchTrack | None = None) -> int:
    """Give every repetition of a line the same notes.

    Per syllable position the consensus pitch (median over the repetitions, octave-folded) is
    used; melismas copy the shape of the best-voiced repetition, scaled to each syllable's
    own timing.  A repetition that clearly differs (key change, variation) is left alone and,
    if ``proposals`` is given, added to it as (that line, a harmonized line) for review.
    Returns the number of lines changed."""
    changed = 0
    for group in _line_groups(syl):
        n = len(group[0])
        if n < config.get_int("harm_min"):
            continue
        consensus = []
        for k in range(n):
            ps = [occ[k].pitch for occ in group if occ[k].voiced >= 0.3] or [occ[k].pitch for occ in group]
            ref = float(np.median(ps))
            c = int(round(float(np.median([_fold(p_, ref) for p_ in ps]))))
            if pitch is not None and config.get("harm_mode") == "pool":
                c = _pooled_pitch(pitch, [(occ[k].start, occ[k].end) for occ in group]) or c
            consensus.append(c)
        best = max(group, key=lambda occ: float(np.mean([x.voiced for x in occ])))
        odd = []
        for occ in group:
            agree = sum(abs(_fold(occ[k].pitch, consensus[k]) - consensus[k]) <= config.get_int("harm_tol") for k in range(n))
            if agree < config.get_float("harm_agree") * n:
                odd.append(occ)
                continue
            for k, x in enumerate(occ):
                tpl = best[k]
                if not config.get_bool("harm_melisma") and any(o[k].segments for o in group):
                    continue  # leave melismas as detected
                if tpl.segments and tpl.end > tpl.start:
                    scale = (x.end - x.start) / (tpl.end - tpl.start)
                    x.segments = [(x.start + (a - tpl.start) * scale, x.start + (b - tpl.start) * scale, m)
                                  for a, b, m in tpl.segments]
                    x.pitch = tpl.segments[0][2]
                else:
                    x.segments = None
                    x.pitch = consensus[k]
            changed += 1
        if proposals is not None and len(odd) < len(group):
            tpl = next(occ for occ in group if not any(occ is o for o in odd))
            proposals.extend((occ, tpl) for occ in odd)
    return changed


GOLDEN_MIN_JUMP = 4       # semitones to a neighbour
GOLDEN_MAX_GAP_S = 0.6    # neighbours further apart are another phrase


def _difficulty(ss: list[Syllable]) -> list[float]:
    """How hard each syllable of a line is to hit: the pitch jump into/out of it (octave
    jumps count as tracker errors) plus how far it sits from the line's usual range."""
    med = float(np.median([x.pitch for x in ss]))
    out = []
    for k, x in enumerate(ss):
        jumps = [0.0]
        for j in (k - 1, k + 1):
            if 0 <= j < len(ss) and (ss[j].start - x.end if j > k else x.start - ss[j].end) <= GOLDEN_MAX_GAP_S:
                d = abs(x.pitch - ss[j].pitch)
                if 11 <= d <= 13:
                    d = abs(d - 12)
                jumps.append(float(d))
        jump = max(jumps)
        out.append(jump + 0.5 * abs(_fold(x.pitch, med) - med) if jump >= GOLDEN_MIN_JUMP else 0.0)
    return out


def mark_note_kinds(syl: list[Syllable]) -> None:
    """Freestyle for unpitched (shouted/spoken) syllables; golden for the hard ones: notes
    reached by a big pitch jump (outliers of their line), held long enough to score, and
    the same notes in every repetition of the line."""
    # freestyle only where really nothing is sung on a pitch: a long unvoiced syllable or a
    # spoken/shouted line.  A short word the pitch tracker missed ("it's") stays a normal note
    # (with its neighbour's pitch) - a freestyle note shows but gives no points.
    line_voiced: dict[int, list[float]] = {}
    for s in syl:
        line_voiced.setdefault(s.line, []).append(s.voiced)
    for s in syl:
        spoken_line = float(np.mean(line_voiced[s.line])) < 0.2
        if s.voiced < FREESTYLE_MAX_VOICED and (s.end - s.start >= FREESTYLE_MIN_S or spoken_line):
            s.kind = "F"
    if config.get("golden") == "legacy":
        return _golden_legacy(syl)
    by_line: dict[int, list[Syllable]] = {}
    for s in syl:
        by_line.setdefault(s.line, []).append(s)
    scored = []
    for ss in by_line.values():
        for x, d in zip(ss, _difficulty(ss)):
            if d > 0 and x.kind == ":" and x.end - x.start >= GOLDEN_MIN_S and x.voiced > 0.6:
                scored.append((d, x))
    scored.sort(key=lambda t: -t[0])
    gold = {id(x) for _, x in scored[: int(GOLDEN_SHARE * len(syl))]}
    for group in _line_groups(syl):  # same golden notes in every repetition
        for k in range(len(group[0])):
            if any(id(occ[k]) in gold for occ in group):
                gold.update(id(occ[k]) for occ in group if occ[k].kind == ":")
    for s in syl:
        if id(s) in gold:
            s.kind = "*"


def _golden_legacy(syl: list[Syllable]) -> None:
    from collections import Counter

    text_of: dict[int, str] = {}
    for s in syl:
        text_of[s.line] = text_of.get(s.line, "") + s.text
    counts = Counter(t.strip().lower() for t in text_of.values())
    cands = [s for s in syl if s.kind == ":" and s.end - s.start >= GOLDEN_MIN_S and s.stability < 0.6
             and s.voiced > 0.8 and counts[text_of[s.line].strip().lower()] > 1]
    cands.sort(key=lambda s: (s.end - s.start) / (0.3 + s.stability), reverse=True)
    for s in cands[: int(GOLDEN_SHARE * len(syl))]:
        s.kind = "*"




def split_lines(syl: list[Syllable]) -> None:
    """Split long lyric lines into screen lines (in place, renumbers ``line``)."""
    words: list[list[Syllable]] = []
    for s in syl:
        if words and words[-1][0].word == s.word:
            words[-1].append(s)
        else:
            words.append([s])

    def text(ws):
        return "".join(x.text for w in ws for x in w).strip()

    def split(ws):
        if len(ws) < 2:
            return [ws]
        dur = ws[-1][-1].end - ws[0][0].start
        if len(text(ws)) <= config.get_int("maxc") and dur <= config.get_float("maxs"):
            return [ws]
        total = len(text(ws))
        best, best_k = None, None
        for k in range(1, len(ws)):
            pause = max(0.0, ws[k][0].start - ws[k - 1][-1].end)
            punct = 0.4 if text(ws[k - 1:k]).rstrip()[-1:] in ",;:.!?)" else 0.0
            balance = abs(len(text(ws[:k])) - len(text(ws[k:]))) / max(1, total)
            single = 0.3 if k == 1 or k == len(ws) - 1 else 0.0
            score = min(pause, 1.0) + punct - 0.6 * balance - single
            if best is None or score > best:
                best, best_k = score, k
        return split(ws[:best_k]) + split(ws[best_k:])

    groups: list[list[list[Syllable]]] = []
    for w in words:
        if groups and groups[-1][0][0].line == w[0].line:
            groups[-1].append(w)
        else:
            groups.append([w])
    new_line = 0
    for g in groups:
        for part in split(g):
            for w in part:
                for x in w:
                    x.line = new_line
            new_line += 1


def to_song(syl: list[Syllable], meta: dict[str, str], audio_name: str, grid=None) -> Song:
    from .tempo import FALLBACK_BPM, Grid

    if grid is None:
        grid = Grid(FALLBACK_BPM, max(0.0, syl[0].start), None)
    gap_s = grid.gap_s
    beat_s = grid.beat_s
    song = Song(headers=[("VERSION", "1.0.0")])
    for key in ("TITLE", "ARTIST", "LANGUAGE", "EDITION", "GENRE", "YEAR"):
        if meta.get(key):
            song.set(key, meta[key])
    song.set("CREATOR", "UltraAI")
    song.set("MP3", audio_name)
    song.set("AUDIO", audio_name)
    for key in ("COVER", "BACKGROUND", "VOCALS", "INSTRUMENTAL"):
        if meta.get(key):
            song.set(key, meta[key])
    song.set("BPM", format_number(grid.bpm, 3))
    song.set("GAP", str(int(round(gap_s * 1000))))
    gap_s = int(round(gap_s * 1000)) / 1000.0  # what USDX will read back

    def beat(t: float) -> int:
        return int(round((t - gap_s) / beat_s))

    lines: list[Line] = []
    line_of: dict[int, Line] = {}   # screen-line number -> Line (for the chorus header)
    prev_end = 0
    current_line = None
    for s in syl:
        if s.line != current_line:
            lines.append(Line())
            line_of[s.line] = lines[-1]
            current_line = s.line
        parts = s.segments or [(s.start, s.end, s.pitch)]
        word_end = s.text.endswith(" ")
        for k, (t0, t1, pitch) in enumerate(parts):
            start = max(beat(t0), prev_end)
            end = max(start + 1, beat(t1))
            if k == 0:
                text = s.text.rstrip() if len(parts) > 1 else s.text
            else:
                text = "~" + (" " if word_end and k == len(parts) - 1 else "")
            lines[-1].notes.append(Note(s.kind, start, end - start, pitch - 60, text, auto_fixed=s.auto_fixed))
            prev_end = end
    # keep a 1-beat gap between notes where possible
    notes = [n for ln in lines for n in ln.notes]
    for a, b in zip(notes, notes[1:]):
        if b.is_continuation:
            continue  # melisma notes stay connected
        if b.start - a.end < 1 and a.length >= 2:
            a.length = max(1, b.start - a.start - 1)
    # line breaks: midpoint for short gaps, ~1 s (max 2 s) after the line for long ones
    for cur, nxt in zip(lines, lines[1:]):
        g = nxt.start - cur.end
        if g <= 1:
            cur.break_beat = cur.end if g >= 0 else nxt.start
        else:
            offset = g / 2 if g * beat_s < 2.0 else min(1.0 / beat_s, g / 2)
            cur.break_beat = min(nxt.start - 1, cur.end + max(1, int(round(offset))))
    if config.get_bool("previewstart"):
        # the chorus = the largest repeated line group (same words + syllable count, as in
        # harmonize_repeats); the preview starts at the first note of its first occurrence
        groups = _line_groups(syl)
        if groups:
            chorus = max(groups, key=lambda g: (len(g), -g[0][0].start))
            first_note = line_of[chorus[0][0].line].notes[0]
            song.set("PREVIEWSTART", f"{song.beat_to_seconds(first_note.start):.2f}")
    song.voices[1] = lines
    return song


@dataclass
class Analysis:
    vocals: Path
    wave: np.ndarray            # mono 16 kHz vocals
    emissions: "torch.Tensor"   # CTC log-probs [T, C]
    pitch: PitchTrack


def analyse(audio: Path, separation: str = DEFAULT_SEPARATION, lead: str | None = None) -> Analysis:
    """Expensive, lyrics-independent steps; every result is cached next to the vocals.

    ``lead``: optional karaoke model to strip backing vocals from the vocal stem first.
    """
    import torch

    from .audio import lead_vocals

    lead = lead if lead is not None else (config.get("lead") or None)
    with stage("separate"):
        vocals, _ = separate(audio, separation, need_instrumental=False)
        if lead:
            vocals, _ = lead_vocals(vocals, lead)
    wave = load_mono(vocals, aligner.SR)
    em_cache = vocals.with_suffix(".emissions.pt")
    with stage("analyse"):
        if em_cache.exists():
            em = torch.load(em_cache)
        else:
            em = aligner.emissions(wave)
            torch.save(em, em_cache)
        p = pitch_track(vocals, wave, config.get("pitch"))
    return Analysis(vocals, wave, em, p)


def pitch_track(vocals: Path, wave, model: str = "swift") -> PitchTrack:
    """Pitch of the vocals with ``model`` (see pitch.py), cached next to them."""
    pitch_cache = vocals.with_suffix(".pitch.npz" if model == "swift" else f".pitch-{model}.npz")
    if pitch_cache.exists():
        z = np.load(pitch_cache)
        return PitchTrack(z["times"], z["midi"], z["confidence"], z["loudness_db"])
    p = track(wave, model)
    np.savez(pitch_cache, times=p.times, midi=p.midi, confidence=p.confidence, loudness_db=p.loudness_db)
    return p


def is_cached(audio: Path, separation: str = DEFAULT_SEPARATION) -> bool:
    from .audio import CACHE_DIR, file_hash

    return (CACHE_DIR / "work" / file_hash(audio) / f"vocals.{separation}.emissions.pt").exists()


def generate(audio: Path, lyrics: Lyrics, meta: dict[str, str],
             separation: str = DEFAULT_SEPARATION) -> tuple[Song, dict]:
    a = analyse(audio, separation)
    if config.get_bool("robust"):
        words = aligner.align_robust(lyrics, a.wave, em=a.emissions)
    else:
        words = aligner.align(lyrics, a.wave, star_between="lines", em=a.emissions)
    syl = build_syllables(lyrics, words, a.pitch)
    fit_syllables(syl, a.pitch)  # keysnap / octave_range (B5b): no-op with the defaults
    debug = {"word_scores": [w.score for w in words]}
    song = to_song(syl, meta, audio.name)
    apply_videogap(song, audio, debug)  # #VIDEOGAP (B5b): no-op without a sibling video
    return song, debug


def lrc_lines(lrc) -> tuple[list[str], list[float] | None]:
    """Clean LRC lines one by one, keeping their timestamps aligned with the text."""
    from .lyrics import clean_lines, strip_echoes

    if lrc.synced:
        texts, times = [], []
        for ln in lrc.lines:
            cleaned = clean_lines([ln.text], drop_echoes=False)
            if cleaned:
                text = strip_echoes(cleaned[0], texts[-1] if texts else "")
                if text:
                    texts.append(text)
                    times.append(ln.time)
        return texts, times
    return clean_lines(lrc.plain), None


def generate_from_lrc(audio: Path, lrc, language: str, meta: dict[str, str],
                      separation: str = DEFAULT_SEPARATION) -> tuple[Song, dict]:
    """M2: lyrics text + line timing from LRC; timing used only if it fits this audio."""
    texts, times = lrc_lines(lrc)
    return generate_from_lines(audio, texts, times, language, meta, separation)


def generate_sung(audio: Path, candidates: list, language: str, meta: dict[str, str],
                  separation: str = DEFAULT_SEPARATION) -> tuple[Song, dict]:
    """M3: listen first.  Whisper transcript decides which lyric version fits and what is
    actually sung (repeats, missing lines); lyrics give the spelling.  Falls back to M2."""
    from .lrc import audio_duration
    from .structure import fill_times, match
    from .transcribe import transcribe

    a = analyse(audio, separation)
    asr_sep = config.get("asr_sep") or None  # benchmarks: one transcript for every separation
    asr_vocals = a.vocals.with_name(f"vocals.{asr_sep}.mono16000.wav") if asr_sep else None
    with stage("listen"):
        if asr_vocals is not None and asr_vocals.exists():
            asr = transcribe(asr_vocals, language)
        else:
            asr = transcribe(a.vocals.with_name(a.vocals.stem + ".mono16000.wav"), language, wave=a.wave)
    scored = []
    for k, cand in enumerate(candidates):
        texts, times = lrc_lines(cand)
        if texts:
            scored.append((match(texts, asr), k, texts, times))
    if not scored:
        raise LookupError("no usable lyrics")
    # the default (M2's) version wins unless another fits the transcript clearly better;
    # a synced version wins over an unsynced one unless the latter is clearly better
    margin = config.get_float("m3_cand_margin")

    def rank(x):
        return x[0].per_word + (margin if x[1] == 0 else 0) + (margin if x[3] is not None else 0)
    scored.sort(key=lambda x: -rank(x))
    st, k, texts, times = scored[0]
    if times is not None and config.get_bool("m3_timed"):
        off = _consensus_offset(st.lines, times)
        if off is not None:  # re-match knowing roughly when each line is sung
            st = match(texts, asr, line_times=times, offset=off)
    info: dict = {"candidate": k, "candidates": len(scored), "asr_words": st.asr_words,
                  "asr_coverage": round(st.coverage, 3), "jumps": st.jumps, "structure": "lrc"}
    min_cov = config.get_float("m3_min_coverage")
    heard = sum(ln.matched for ln in st.lines) / max(1, sum(ln.words for ln in st.lines))
    info["heard"] = round(heard, 3)
    trusted = False
    if st.coverage >= min_cov and heard >= config.get_float("m3_min_heard") \
            and len(st.lines) >= 3:
        order = [ln.line for ln in st.lines]
        same = order == list(range(len(texts)))
        mode = config.get("m3_times")
        if same and times is not None and mode == "hybrid":
            info["structure"] = "lrc-confirmed"
            off = _consensus_offset(st.lines, times)
            if off is not None and abs(off) > LRC_SHIFT_S:  # LRC made for another release
                times = [t + off for t in times]
                info["lrc_shift"] = round(off, 2)
            trusted = off is not None
        else:
            lrc_times = times
            if lrc_times is not None:
                st.lines = _drop_weak_repeats(st.lines)
            texts = [ln.text for ln in st.lines]
            times = fill_times(st.lines, audio_duration(audio) or len(a.wave) / 16000)
            if lrc_times is not None and config.get_bool("m3_lrc_fill"):
                times = _merge_lrc_times(st.lines, times, lrc_times)
            info["structure"] = "sung"
            trusted = True
    flags = None
    if len(st.lines) == len(texts) and all(ln.text == t for ln, t in zip(st.lines, texts)):
        seen: set[int] = set()
        flags = []
        for ln in st.lines:
            flags.append({"heard": ln.matched / max(1, ln.words), "repeat": ln.line in seen})
            seen.add(ln.line)
    asr_words = [w for seg in asr for w in seg.words]
    song, more = generate_from_lines(audio, texts, times, language, meta, separation,
                                     trusted=trusted and config.get_bool("m3_trust"),
                                     asr_words=asr_words, line_flags=flags)
    more.update(info)
    return song, more


LRC_AGREE_S = 2.5
LRC_SHIFT_S = 1.0


def _well_heard(ln) -> bool:
    """Enough of the line was recognised for its transcript time to mean something."""
    return ln.start is not None and ln.matched >= min(ln.words, 2) and ln.matched >= 0.4 * ln.words


def _drop_weak_repeats(lines):
    """Extra occurrences of a line (beyond the synced lyrics) need real evidence: drop
    repeats that are not well heard, keeping one occurrence of every line."""
    seen: dict[int, int] = {}
    for ln in lines:
        seen[ln.line] = seen.get(ln.line, 0) + 1
    kept, have = [], set()
    for k, ln in enumerate(lines):
        if seen[ln.line] > 1 and not _well_heard(ln):
            later = any(o.line == ln.line for o in lines[k + 1:])
            if ln.line in have or later:
                continue
        kept.append(ln)
        have.add(ln.line)
    return kept


def _consensus_offset(lines, lrc_times: list[float], tol: float = 1.0) -> float | None:
    """Transcript time minus LRC time that most once-sung, well-heard lines agree on."""
    counts: dict[int, int] = {}
    for ln in lines:
        counts[ln.line] = counts.get(ln.line, 0) + 1
    diffs = sorted(ln.start - lrc_times[ln.line] for ln in lines
                   if counts[ln.line] == 1 and _well_heard(ln))
    if not diffs:
        return None
    best = max(diffs, key=lambda d: sum(abs(x - d) <= tol for x in diffs))
    return float(np.median([x for x in diffs if abs(x - best) <= tol]))


def _merge_lrc_times(lines, asr_times: list[float], lrc_times: list[float]) -> list[float]:
    """Line times for the sung script, in the LRC's time base.

    Whisper is good at *which* lines are sung but its timing is loose (held notes, echoes),
    so an LRC timestamp wins wherever it agrees with the transcript; the transcript time is
    used for lines the LRC places elsewhere or does not have (extra repeats).  Weakly heard
    lines (one stray word) do not count as heard.
    """
    off = _consensus_offset(lines, lrc_times) or 0.0
    # an LRC made for another release (longer intro...) is shifted into audio time; small
    # offsets are mostly Whisper's own latency and are left to the aligner's anchoring
    shift = off if abs(off) > LRC_SHIFT_S else 0.0
    lrc_times = [t + shift for t in lrc_times]
    off -= shift
    heard = [_well_heard(ln) for ln in lines]
    # each LRC line time may be used once: by the occurrence closest to it
    owner: dict[int, int] = {}
    for i, ln in enumerate(lines):
        t = asr_times[i] - off
        j = owner.get(ln.line)
        if j is None or abs(t - lrc_times[ln.line]) < abs(asr_times[j] - off - lrc_times[ln.line]):
            owner[ln.line] = i
    out: list[float | None] = []
    for i, ln in enumerate(lines):
        asr_t = asr_times[i] - off
        lrc_t = lrc_times[ln.line]
        if owner.get(ln.line) == i and (not heard[i] or abs(asr_t - lrc_t) < LRC_AGREE_S):
            out.append(lrc_t)
        elif heard[i]:
            out.append(asr_t)
        else:
            out.append(None)  # weakly heard extra occurrence: interpolate below
    known = [i for i, t in enumerate(out) if t is not None]
    for i, t in enumerate(out):
        if t is None:
            prev = max((k for k in known if k < i), default=None)
            nxt = min((k for k in known if k > i), default=None)
            if prev is None:
                out[i] = max(0.0, out[nxt] - 3.0 * (nxt - i))
            elif nxt is None:
                out[i] = out[prev] + 3.0 * (i - prev)
            else:
                out[i] = out[prev] + (out[nxt] - out[prev]) * (i - prev) / (nxt - prev)
    out[0] = max(0.0, out[0])
    for i in range(1, len(out)):  # keep monotonic
        out[i] = max(out[i], out[i - 1])
    return out


def _syl_pitch(p: PitchTrack, s: Syllable) -> float | None:
    a, b = p.index(s.start), max(p.index(s.end), p.index(s.start) + 1)
    seg = p.midi[a:b][p.voiced()[a:b]]
    return float(np.median(seg)) if len(seg) >= 3 else None


def _rms_db(wave, s: Syllable) -> float:
    seg = wave[int(s.start * 16000): int(s.end * 16000)]
    return 20 * float(np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)) if len(seg) else -120.0


def _models(key: str) -> list[str]:
    return config.get_list(key)


def _pitch_name(m: str) -> str:
    from .models import name

    return name("pitch", m)


def double_check_issues(audio: Path, separation: str, syl: list[Syllable], a) -> list[dict]:
    """Second (third, ...) opinions, all optional (worker options):
    ULTRAAI_PITCH_CHECK=m1,m2,...: more pitch models - syllables where any of them disagrees
      with the main model by 2+ semitones (octave ignored) are marked.  When >=2 of the check
      models agree with each other (within 1 semitone, octave-folded) and their consensus
      differs from the main pitch by 2+ semitones, the syllable pitch is auto-corrected to
      that consensus instead and the issue is recorded as kind 'auto-fixed'
      (ULTRAAI_CHECK_AUTOFIX, default on; only active with more than one check model);
    ULTRAAI_SEP_CHECK=m1,m2,...: more vocal separations - syllables that are clearly sung in
      one separation but (nearly) silent or on another pitch in another are marked (backing
      vocals, instruments or noise that one model let through)."""
    from . import issues as iss
    from .models import name as model_name

    main_pitch = config.get("pitch")
    out = []
    checks = [m for m in _models("pitch_check") + _models("pitch2") if m != main_pitch]
    others = {m: pitch_track(a.vocals, a.wave, m) for m in dict.fromkeys(checks)}
    if others:
        autofix_on = config.get_bool("check_autofix")
        for s in syl:
            x = _syl_pitch(a.pitch, s)
            if x is None:
                continue
            folded = {m: _fold(y, x) for m, p2 in others.items() if (y := _syl_pitch(p2, s)) is not None}
            fixed = iss.consensus_autofix(x, folded) if autofix_on else None
            if fixed is not None:
                consensus, models = fixed
                new_pitch = int(round(consensus))
                delta = new_pitch - s.pitch
                s.pitch = new_pitch
                if s.segments:  # keep the melisma shape, shift it with the syllable
                    s.segments = [(t0, t1, p + delta) for t0, t1, p in s.segments]
                s.auto_fixed = True
                out.append(iss.autofix_issue(s, [_pitch_name(m) for m in models],
                                             int(round(x)), new_pitch, _pitch_name(main_pitch)))
                continue
            bad = {m: abs(f - x) for m, f in folded.items() if abs(f - x) >= 2}
            if bad:
                d = max(bad.values())
                who = ", ".join(f"{_pitch_name(m)} {round(v)}" for m, v in bad.items())
                out.append(_note_issue("models_pitch", s, 0.6 + min(0.25, 0.05 * (d - 2)) + 0.05 * (len(bad) - 1),
                                       f"pitch models disagree with {_pitch_name(main_pitch)} – semitones off: {who}"))
    seps = [m for m in _models("sep_check") + _models("sep2") if m != separation]
    for s2 in dict.fromkeys(seps):
        v2, _ = separate(audio, s2, need_instrumental=False)
        w2 = load_mono(v2, aligner.SR)
        p2 = pitch_track(v2, w2, main_pitch)
        name = model_name("separation", s2)
        for s in syl:
            e1, e2 = _rms_db(a.wave, s), _rms_db(w2, s)
            if max(e1, e2) > -40 and abs(e1 - e2) >= 15:
                out.append(_note_issue("models_sep", s, 0.7, f"{name} {'hears' if e2 > e1 else 'does not hear'} singing "
                                       f"here ({round(abs(e1 - e2))} dB difference) – backing vocals or noise?"))
                continue
            x, y = _syl_pitch(a.pitch, s), _syl_pitch(p2, s)
            if x is not None and y is not None and abs(_fold(y, x) - x) >= 2:
                out.append(_note_issue("models_sep", s, 0.6, f"the vocals from {name} have another pitch here"))
    return _merge_note_issues(out)


def _note_issue(kind: str, s: Syllable, risk: float, why: str) -> dict:
    return {"kind": kind, "line": s.line, "start": round(s.start, 3), "end": round(s.end, 3),
            "risk": round(risk, 3), "text": s.text.strip(), "reasons": [why]}


def _merge_note_issues(issues: list[dict]) -> list[dict]:
    """Neighbouring syllables with the same finding become one issue."""
    out: list[dict] = []
    for i in sorted(issues, key=lambda x: (x["kind"], x["start"])):
        if i["kind"] == "auto-fixed":  # one entry per corrected note (models/old/new pitch)
            out.append(dict(i))
            continue
        last = out[-1] if out else None
        if last and last["kind"] == i["kind"] and last["line"] == i["line"] and i["start"] - last["end"] < 0.6:
            last["end"] = i["end"]
            last["text"] += " " + i["text"]
            last["risk"] = max(last["risk"], i["risk"])
            continue
        out.append(dict(i))
    return out


def _repeat_issues(proposals) -> list[dict]:
    """Repeats sung (or detected) differently from the others: ask the user."""
    out = []
    for occ, tpl in proposals:
        m, s_ = divmod(int(tpl[0].start), 60)
        out.append({"kind": "repeat", "line": occ[0].line, "start": round(occ[0].start, 3),
                    "end": round(occ[-1].end, 3), "risk": 0.55,
                    "text": "".join(x.text for x in occ).strip(), "template_start": round(tpl[0].start, 3),
                    "reasons": [f"same words as at {m}:{s_:02d}, but different notes – make them the same?"]})
    return out


def _add_issues(info, syl, free, expected, asr_words, line_flags, lyrics, pitch=None) -> None:
    """Per-word risk + review issues for the editor (see issues.py)."""
    from . import issues as iss

    free_starts = {w.index: w.start for w in free}
    words = iss.word_features(syl, free_starts, expected, asr_words, line_flags,
                              anchored=info.get("anchored", False), sung=line_flags is not None,
                              pitch=pitch)
    model = iss.load_model()
    iss.score(words, model)
    left = iss.leftover_asr(asr_words, [(w.start, w.text) for w in words]) if asr_words else []
    info["words"] = iss.to_json(words)
    info["issues"] = iss.line_issues(words, (model or {}).get("threshold", 0.5), left)
    info["risk_model"] = (model or {}).get("version", "rules")


def generate_from_lines(audio: Path, texts: list[str], times: list[float] | None, language: str,
                        meta: dict[str, str], separation: str = DEFAULT_SEPARATION,
                        trusted: bool = False, asr_words: list | None = None,
                        line_flags: list[dict] | None = None) -> tuple[Song, dict]:
    """Align given lyric lines (optionally with rough line start times) to the audio.

    ``trusted``: the times were checked against the audio (transcript), so anchor to them
    even when the free alignment is too poor to confirm them."""
    from .lyrics import prepare

    lyrics = prepare(texts, language)
    assert len(lyrics.lines) == len(texts)
    a = analyse(audio, separation)
    with stage("align"):
        words = aligner.align(lyrics, a.wave, star_between="lines", em=a.emissions)
    free = words
    expected: list[float | None] | None = None
    info: dict = {"lrc_synced": times is not None, "anchored": False}
    if times is not None:
        offsets = aligner.lrc_offsets(lyrics, words, times)
        if offsets is None and trusted:
            offsets = [0.0] * len(times)
            info["trusted_anchors"] = True
        if offsets is not None:
            # lines timed after the end of this audio (LRC of a longer release / fade-out
            # edit) are not sung here: drop them, otherwise no anchored path exists
            dur = len(a.wave) / 16000
            keep = [i for i, t in enumerate(times) if t + offsets[i] < dur - 1.0]
            if keep and len(keep) < len(times) and keep == list(range(len(keep))):
                info["cut_lines"] = len(times) - len(keep)
                texts, times, offsets = texts[:len(keep)], times[:len(keep)], offsets[:len(keep)]
                if line_flags:
                    line_flags = line_flags[:len(keep)]
                lyrics = prepare(texts, language)
                with stage("align"):
                    words = free = aligner.align(lyrics, a.wave, star_between="lines", em=a.emissions)
            if config.get_bool("repair"):
                times, info["repaired_lines"] = aligner.repair_anchors(
                    lyrics, words, times, offsets,
                    min_conf=config.get_float("repair_conf"),
                    window=config.get_float("repair_shift"))
            for window in (aligner.ANCHOR_WINDOW_S, 3.0, 6.0):
                try:
                    with stage("align"):
                        words = aligner.align_anchored(
                            lyrics, a.emissions, times, offsets, window=window,
                            asr_words=asr_words if config.get_float("asr_prior") > 0 else None)
                    info.update(anchored=True, anchor_window=window, offset_median=float(np.median(offsets)))
                    expected = [t + o for t, o in zip(times, offsets)]
                    break
                except ValueError:  # LRC times inconsistent with this audio: loosen
                    continue
            else:
                with stage("align"):
                    words = aligner.align_robust(lyrics, a.wave, em=a.emissions)
    else:
        with stage("align"):
            words = aligner.align_robust(lyrics, a.wave, em=a.emissions)
    with stage("notes"):
        if config.get_bool("onset_snap"):
            words, info["onset_snap"] = snap_words_to_onsets(
                words, onsets_for(a.vocals, a.wave), config.get_float("onset_snap_win"))
        syl = build_syllables(lyrics, words, a.pitch)
        syl, info["dropped_vocable_lines"] = drop_unsung_vocables(syl)
        split_lines(syl)
        proposals: list = []
        if config.get_bool("harmonize"):
            info["harmonized_lines"] = harmonize_repeats(syl, proposals, a.pitch)
        mark_note_kinds(syl)
        info["lines"] = line_report(syl)
        info["low_confidence_lines"] = sum(r["review"] for r in info["lines"])
        _add_issues(info, syl, free, expected, asr_words, line_flags, lyrics, a.pitch)
        info["issues"] += double_check_issues(audio, separation, syl, a)
        info["issues"] = sorted(info["issues"] + _repeat_issues(proposals), key=lambda i: -i["risk"])

        # B5b: final pitch fit (keysnap / octave range, both off by default) — after the
        # last pitch change (double-check auto-fix), before the song is built; timing untouched
        info["pitch_fit"] = fit_syllables(syl, a.pitch)
        grid = None
        if config.get_bool("tempo"):
            from .audio import work_dir
            from .tempo import choose_grid, detect_beats, load_onsets

            mix = work_dir(audio) / "mix.wav"
            if not mix.exists() and not mix.with_suffix(".beats.npz").exists():
                from .audio import to_wav

                to_wav(audio, mix)  # the library run deletes decoded mixes to save disk
            tempo, beats = detect_beats(mix)
            onsets, strengths = load_onsets(mix)
            grid = choose_grid(tempo, beats, syl[0].start, onsets=onsets, strengths=strengths)
            info["tempo"] = grid.tempo
        song = to_song(syl, meta, audio.name, grid)
        apply_videogap(song, audio, info)  # #VIDEOGAP (B5b): only when a sibling video exists
        return song, info
