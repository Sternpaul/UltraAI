"""Compare a generated song (hypothesis) against a hand-made reference.

All comparisons happen in seconds, so the two files may use different BPM/GAP.

Metrics
- words:     recall/precision of lyric tokens (should be ~1.0: lyrics are given)
- onset:     word-start error of matched words (median, p90, % within 50/100/250 ms)
- syllable:  onset error of inner syllables for words split the same way
- lines:     line-start agreement (precision/recall/F1 over matched words)
- pitch:     frame-wise note activity F1 and pitch-class accuracy where both sing
- score:     share of our pitched note time a "perfect singer of the reference" would hit
             (USDX medium difficulty: octave-free, +-1 semitone)
- grid:      grid_bpm_ratio (hyp #BPM / ref #BPM) and grid_phase_ms (signed offset of the
             reference's first note from the nearest hyp beat pulse) — null unless both
             sides have #BPM, #GAP and notes
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher

from ..ultrastar.model import Song, Word

FRAME = 0.01  # seconds


def norm_token(text: str) -> str:
    """Rough phonetic key so spelling variants match (incl. Greek script vs. Greeklish)."""
    from ..lyrics import romanize

    t = romanize(text).replace("'", "")
    for a, b in (("ch", "x"), ("kh", "x"), ("ou", "u"), ("ei", "i"), ("oi", "i"), ("y", "i"),
                 ("ph", "f"), ("ck", "k")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


@dataclass
class TimedWord:
    token: str
    start: float
    end: float
    syllable_starts: list[float]
    syllable_texts: list[str]
    line_index: int


def timed_words(song: Song, voice: int = 1, lang: str = "en") -> list[TimedWord]:
    from ..lyrics import expand_numbers

    out = []
    for w in song.words(voice):
        tok = norm_token(expand_numbers(w.text, lang))  # "22" and "twenty-two" must match
        if not tok:
            continue
        out.append(_timed(song, w, tok, lang))
    return out


def _timed(song: Song, w: Word, tok: str, lang: str = "en") -> TimedWord:
    from ..lyrics import expand_numbers

    syl = w.syllables
    return TimedWord(
        token=tok,
        start=song.beat_to_seconds(w.start),
        end=song.beat_to_seconds(w.end),
        syllable_starts=[song.beat_to_seconds(n.start) for n in syl],
        syllable_texts=[norm_token(expand_numbers(n.text, lang)) for n in syl],
        line_index=w.line_index,
    )


def match_words(ref: list[TimedWord], hyp: list[TimedWord], time_scale: float = 5.0) -> list[tuple[int, int]]:
    """Monotonic word matching that prefers time-near matches.

    Plain longest-common-subsequence is ambiguous for repeated choruses: it can pair our
    2nd chorus with the reference's 3rd and report a 50 s error.  Each match scores
    1 + 0.5*exp(-|dt|/time_scale), so the number of matches dominates but, among equally long
    matchings, the one closest in time wins.  Without times (no .start), falls back to LCS.
    """
    n, m = len(ref), len(hyp)
    if not n or not m:
        return []
    timed = all(hasattr(w, "start") for w in ref) and all(hasattr(w, "start") for w in hyp)
    if not timed:
        sm = SequenceMatcher(None, [w.token for w in ref], [w.token for w in hyp], autojunk=False)
        return [(b.a + k, b.b + k) for b in sm.get_matching_blocks() for k in range(b.size)]
    import math
    stride = m + 1
    back = bytearray((n + 1) * stride)  # 0 diag, 1 up, 2 left - one uint8 per cell
    prev = [0.0] * stride
    for i in range(1, n + 1):
        ri, row, base = ref[i - 1], [0.0] * stride, i * stride
        for j in range(1, m + 1):
            best, how = prev[j], 1
            if row[j - 1] > best:
                best, how = row[j - 1], 2
            hj = hyp[j - 1]
            if ri.token == hj.token:
                v = prev[j - 1] + 1.0 + 0.5 * math.exp(-abs(hj.start - ri.start) / time_scale)
                if v > best:
                    best, how = v, 0
            row[j] = best
            back[base + j] = how
        prev = row
    pairs, i, j = [], n, m
    while i and j:
        how = back[i * stride + j]
        if how == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def _err_stats(errors: list[float], prefix: str) -> dict[str, float]:
    if not errors:
        return {f"{prefix}_n": 0}
    abs_e = sorted(abs(e) for e in errors)
    return {
        f"{prefix}_n": len(errors),
        f"{prefix}_median_ms": 1000 * statistics.median(abs_e),
        f"{prefix}_p90_ms": 1000 * abs_e[min(len(abs_e) - 1, int(0.9 * len(abs_e)))],
        f"{prefix}_bias_ms": 1000 * statistics.median(errors),
        f"{prefix}_within_50ms": sum(e <= 0.05 for e in abs_e) / len(abs_e),
        f"{prefix}_within_100ms": sum(e <= 0.10 for e in abs_e) / len(abs_e),
        f"{prefix}_within_250ms": sum(e <= 0.25 for e in abs_e) / len(abs_e),
    }


def _frames(song: Song, voice: int, pitched_only: bool) -> dict[int, int]:
    frames: dict[int, int] = {}
    for n in song.notes(voice):
        if pitched_only and not n.is_pitched:
            continue
        a = round(song.beat_to_seconds(n.start) / FRAME)
        b = round(song.beat_to_seconds(n.end) / FRAME)
        for f in range(a, b):
            frames[f] = n.pitch
    return frames


def _pc_dist(a: int, b: int) -> int:
    d = (a - b) % 12
    return min(d, 12 - d)


def _grid_metrics(ref: Song, hyp: Song, voice: int) -> dict[str, float | None]:
    """Beat-grid agreement with the reference; nulls when either side lacks #BPM/#GAP/notes.

    grid_bpm_ratio: hyp #BPM / ref #BPM (1.0 = same tempo).
    grid_phase_ms:  phase offset between the two grids, evaluated at the reference's
                    first note (voice ``voice``): the signed distance in ms from that
                    note's time to the nearest hyp beat pulse (positive = the note falls
                    after the nearest hyp pulse). 0 = the grids coincide there.
    """
    out: dict[str, float | None] = {"grid_bpm_ratio": None, "grid_phase_ms": None}
    if not (ref.has("BPM") and hyp.has("BPM") and ref.has("GAP") and hyp.has("GAP")):
        return out
    rn = list(ref.notes(voice))
    hn = list(hyp.notes(voice))
    if not rn or not hn:
        return out
    out["grid_bpm_ratio"] = hyp.bpm / ref.bpm
    t0 = ref.beat_to_seconds(min(n.start for n in rn))
    p = hyp.seconds_per_beat
    u = (t0 - hyp.gap_ms / 1000.0) / p
    frac = u - round(u)  # in [-0.5, 0.5): offset of t0 from the nearest hyp pulse, in beats
    out["grid_phase_ms"] = 1000.0 * frac * p
    return out


def compare(ref: Song, hyp: Song, voice: int = 1) -> dict[str, float | None]:
    from ..lyrics import language_code

    # expand numeral tokens on both sides so "22" == "twenty two" (lang from the song metadata)
    lang = language_code(ref.get("LANGUAGE")) if ref.get("LANGUAGE") else "en"
    rw, hw = timed_words(ref, voice, lang), timed_words(hyp, voice, lang)
    if "greek" in (ref.get("LANGUAGE") or "").lower():
        # Greeklish writes chi as h, ch or x: compare on one spelling
        for w in rw + hw:
            w.token = w.token.replace("x", "h")
    pairs = match_words(rw, hw)
    m: dict[str, float | None] = {
        "ref_words": len(rw),
        "hyp_words": len(hw),
        "word_recall": len(pairs) / len(rw) if rw else 0.0,
        "word_precision": len(pairs) / len(hw) if hw else 0.0,
    }

    onset = [hw[j].start - rw[i].start for i, j in pairs]
    m.update(_err_stats(onset, "onset"))
    # same, after removing the song's constant offset (references are often shifted as a whole)
    near = [e for e in onset if abs(e) < 1.5]  # whole-file shifts of up to ~1 s are common
    shift = statistics.median(near) if near else 0.0
    m["constant_offset_ms"] = 1000 * shift
    m.update(_err_stats([e - shift for e in onset], "onset_rel"))
    m.update(_err_stats([hw[j].end - rw[i].end for i, j in pairs], "offset"))

    inner, same_split, comparable = [], 0, 0
    for i, j in pairs:
        r, h = rw[i], hw[j]
        if len(r.syllable_starts) > 1 or len(h.syllable_starts) > 1:
            comparable += 1
            if r.syllable_texts == h.syllable_texts:
                same_split += 1
        if len(r.syllable_starts) == len(h.syllable_starts):
            inner.extend(hs - rs for rs, hs in zip(r.syllable_starts[1:], h.syllable_starts[1:]))
    m.update(_err_stats(inner, "syllable"))
    m["syllable_split_agreement"] = same_split / comparable if comparable else 1.0

    # Line starts among matched words.
    def starts(words: list[TimedWord]) -> set[int]:
        return {k for k in range(len(words)) if k == 0 or words[k].line_index != words[k - 1].line_index}

    rs, hs = starts(rw), starts(hw)
    tp = sum(1 for i, j in pairs if i in rs and j in hs)
    ref_n = sum(1 for i, _ in pairs if i in rs)
    hyp_n = sum(1 for _, j in pairs if j in hs)
    p = tp / hyp_n if hyp_n else 0.0
    r = tp / ref_n if ref_n else 0.0
    m.update({"line_precision": p, "line_recall": r,
              "line_f1": 2 * p * r / (p + r) if p + r else 0.0,
              "ref_lines": len(ref.lines(voice)), "hyp_lines": len(hyp.lines(voice))})

    # Frame-wise activity and pitch.
    ra, ha = _frames(ref, voice, False), _frames(hyp, voice, False)
    both = ra.keys() & ha.keys()
    p = len(both) / len(ha) if ha else 0.0
    r = len(both) / len(ra) if ra else 0.0
    m["activity_f1"] = 2 * p * r / (p + r) if p + r else 0.0

    rp, hp = _frames(ref, voice, True), _frames(hyp, voice, True)
    common = rp.keys() & hp.keys()
    if common:
        m["pitch_class_acc"] = sum(_pc_dist(rp[f], hp[f]) == 0 for f in common) / len(common)
        m["pitch_class_acc_1"] = sum(_pc_dist(rp[f], hp[f]) <= 1 for f in common) / len(common)
        m["octave_offset"] = statistics.median(round((hp[f] - rp[f]) / 12) for f in common)
    if hp:
        m["perfect_singer_score"] = sum(1 for f, pitch in hp.items()
                                        if f in rp and _pc_dist(rp[f], pitch) <= 1) / len(hp)

    m.update(_grid_metrics(ref, hyp, voice))
    return m


SUMMARY_KEYS = [
    ("word_recall", "{:.1%}"), ("onset_median_ms", "{:.0f}"), ("onset_within_100ms", "{:.1%}"),
    ("onset_within_250ms", "{:.1%}"), ("onset_rel_within_100ms", "{:.1%}"),
    ("constant_offset_ms", "{:+.0f}"), ("syllable_median_ms", "{:.0f}"),
    ("syllable_split_agreement", "{:.1%}"), ("line_f1", "{:.1%}"), ("activity_f1", "{:.1%}"),
    ("pitch_class_acc", "{:.1%}"), ("perfect_singer_score", "{:.1%}"),
]


def format_summary(m: dict[str, float]) -> str:
    parts = []
    for key, fmt in SUMMARY_KEYS:
        if key in m:
            parts.append(f"{key}={fmt.format(m[key])}")
    return "  ".join(parts)
