"""Where is the automatic result probably wrong?  Per-word evidence -> risk -> review issues.

Independent sources of timing/text evidence disagree where we are likely wrong:
  - acoustic alignment confidence (CTC) and voicing of the sung word
  - the free (unanchored) alignment vs the final anchored one
  - the LRC line time vs where the line ended up
  - Whisper: did it hear this word near this time?  Did it hear the line at all?
  - Whisper words that match no lyric word (sung text missing from the lyrics)
A logistic model (fit on the library: hand-made files say where we were wrong) turns the
features into a risk per word; words are grouped into issues per screen line for the editor.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

MODEL_PATH = Path(__file__).with_name("issue_model.pkl")  # scripts/issue_calibrate.py --save
CAP = 5.0          # seconds: cap for distance features
ASR_SIM = 0.6      # text similarity for "Whisper heard this word"
ASR_NEAR = 0.5     # s: heard *here*

FEATURES = [
    "ctc", "ctc_line_min", "voiced", "log_dur", "n_syl", "free_dev", "free_dev_line",
    "asr_dt", "asr_none", "asr_line_heard", "lrc_dev", "has_times", "anchored",
    "struct_heard", "repeat", "sung_structure", "gap_prev", "gap_next", "stability",
    "near_leftover", "line_pos", "loud_rel", "asr_prob", "asr_span_match", "text_repeats",
    "word_repeat_near", "syl_rate_line",
]


def _key(text: str) -> str:
    from .eval.metrics import norm_token

    return norm_token(text)


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass
class WordInfo:
    text: str
    start: float
    end: float
    line: int                 # screen line (output)
    src_line: int             # lyric line given to the aligner
    f: dict[str, float] = field(default_factory=dict)
    risk: float = 0.0
    reasons: list[str] = field(default_factory=list)


def leftover_asr(asr_words: list, lyric_words: list[tuple[float, str]], window: float = 3.0,
                 min_prob: float = 0.4) -> list[tuple[float, float, str]]:
    """Runs of >= 3 confidently transcribed words that match no lyric word nearby:
    probably sung text the lyrics do not contain.  Returns (start, end, text)."""
    keys = [(t, _key(w)) for t, w in lyric_words]
    runs, cur = [], []
    for w in asr_words:
        k = _key(w.text)
        if not k:
            continue
        hit = any(abs(t - w.start) <= window and _sim(k, lk) >= ASR_SIM for t, lk in keys)
        if not hit and w.prob >= min_prob:
            cur.append(w)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = []
    if len(cur) >= 3:
        runs.append(cur)
    return [(r[0].start, r[-1].end, " ".join(w.text for w in r)) for r in runs]


def word_features(syl, free_starts: dict[int, float], expected: list[float | None], asr_words: list | None,
                  line_flags: list[dict] | None, anchored: bool, sung: bool, pitch=None) -> list[WordInfo]:
    """Features per output word.  ``syl``: final syllables (in order, after line splitting);
    ``free_starts``: lyric word index -> start in the unanchored alignment; ``expected``: per
    lyric line, the start the LRC/transcript predicted (None if unknown); ``asr_words``:
    Whisper words (None if no transcript); ``line_flags``: per lyric line, structure info."""
    import numpy as np

    words: list[list] = []
    for s in syl:
        if words and words[-1][0].word == s.word:
            words[-1].append(s)
        else:
            words.append([s])
    out: list[WordInfo] = []
    for ss in words:
        text = "".join(x.text for x in ss).strip()
        wi = WordInfo(text, ss[0].start, ss[-1].end, ss[0].line, getattr(ss[0], "src_line", ss[0].line))
        dur = max(1e-3, ss[-1].end - ss[0].start)
        wi.f.update(
            ctc=float(ss[0].score), voiced=float(np.mean([x.voiced for x in ss])),
            log_dur=math.log(dur), n_syl=float(len(ss)),
            stability=float(min(5.0, np.mean([min(x.stability, 5.0) for x in ss]))),
            free_dev=min(CAP, abs(ss[0].start - free_starts[ss[0].word])) if ss[0].word in free_starts else CAP,
            anchored=float(anchored), sung_structure=float(sung))
        out.append(wi)
    # neighbours, line aggregates
    by_src: dict[int, list[WordInfo]] = {}
    by_line: dict[int, list[WordInfo]] = {}
    for w in out:
        by_src.setdefault(w.src_line, []).append(w)
        by_line.setdefault(w.line, []).append(w)
    for k, w in enumerate(out):
        w.f["gap_prev"] = min(CAP, w.start - out[k - 1].end) if k else CAP
        w.f["gap_next"] = min(CAP, out[k + 1].start - w.end) if k + 1 < len(out) else CAP
        ln = by_line[w.line]
        w.f["line_pos"] = ln.index(w) / max(1, len(ln) - 1)
    for src, ws in by_src.items():
        e = expected[src] if expected and src < len(expected) else None
        dev = min(CAP, abs(ws[0].start - e)) if e is not None else 0.0
        fl = (line_flags[src] if line_flags and src < len(line_flags) else None) or {}
        for w in ws:
            w.f["lrc_dev"] = dev
            w.f["has_times"] = float(e is not None)
            w.f["struct_heard"] = float(fl.get("heard", 1.0))
            w.f["repeat"] = float(fl.get("repeat", False))
    for ws in by_line.values():
        mn = min(w.f["ctc"] for w in ws)
        fd = float(np.median([w.f["free_dev"] for w in ws]))
        for w in ws:
            w.f["ctc_line_min"] = mn
            w.f["free_dev_line"] = fd
    # repetitive text: where words get paired with the wrong repetition
    from collections import Counter

    line_text = {li: " ".join(_key(w.text) for w in ws) for li, ws in by_line.items()}
    counts = Counter(line_text.values())
    keys = [_key(w.text) for w in out]
    for k, w in enumerate(out):
        w.f["text_repeats"] = float(min(5, counts[line_text[w.line]] - 1))
        near = keys[max(0, k - 3):k] + keys[k + 1:k + 4]
        w.f["word_repeat_near"] = float(keys[k] in near)
    for ws in by_line.values():
        span = max(0.3, ws[-1].end - ws[0].start)
        rate = sum(w.f["n_syl"] for w in ws) / span
        for w in ws:
            w.f["syl_rate_line"] = min(15.0, rate)
    # vocal loudness relative to the song (is anyone singing here?)
    if pitch is not None:
        loud = pitch.loudness_db
        med = float(np.nanmedian(loud[pitch.confidence >= 0.5])) if (pitch.confidence >= 0.5).any() else 0.0
        for w in out:
            a, b = pitch.index(w.start), max(pitch.index(w.end), pitch.index(w.start) + 1)
            seg = loud[a:b]
            w.f["loud_rel"] = float(np.clip(np.nanmean(seg) - med, -40, 20)) if len(seg) else 0.0
    # Whisper evidence
    left = []
    if asr_words:
        aw = [(w.start, _key(w.text), w.prob) for w in asr_words if _key(w.text)]
        for w in out:
            k = _key(w.text)
            best, prob = CAP, 0.0
            for t, ak, pr in aw:
                if abs(t - w.start) < best and _sim(k, ak) >= ASR_SIM:
                    best, prob = abs(t - w.start), pr
            w.f["asr_dt"] = best
            w.f["asr_none"] = float(best >= CAP)
            w.f["asr_prob"] = prob
        # Whisper words inside a screen line's span: do they belong to that line?
        for ws in by_line.values():
            a, b = ws[0].start - 0.3, ws[-1].end + 0.3
            inside = [ak for t, ak, _ in aw if a <= t <= b]
            lk = [_key(w.text) for w in ws]
            m = sum(any(_sim(ak, x) >= ASR_SIM for x in lk) for ak in inside) / len(inside) if inside else 1.0
            for w in ws:
                w.f["asr_span_match"] = m
        left = leftover_asr(asr_words, [(w.start, w.text) for w in out])
    for ws in by_src.values():
        heard = sum(w.f.get("asr_dt", CAP) < ASR_NEAR for w in ws) / len(ws)
        for w in ws:
            w.f["asr_line_heard"] = heard if asr_words else 1.0
            if not asr_words:
                w.f["asr_dt"], w.f["asr_none"] = 0.0, 0.0
    for w in out:
        w.f["near_leftover"] = float(any(a - 2.0 <= w.start <= b + 2.0 for a, b, _ in left))
    return out


def _vector(f: dict[str, float]) -> list[float]:
    return [float(f.get(k, 0.0)) for k in FEATURES]


_MODEL: dict | None = None


def load_model(path: Path = MODEL_PATH) -> dict | None:
    global _MODEL
    if _MODEL is None and path.exists():
        import pickle

        _MODEL = pickle.loads(path.read_bytes())
    return _MODEL


def score(words: list[WordInfo], model: dict | None = None) -> None:
    """Fill ``risk`` (0..1, probability the word is badly timed or wrong) and ``reasons``."""
    model = model if model is not None else load_model()
    if model is not None and words:
        import numpy as np

        X = np.array([[float(w.f.get(k, 0.0)) for k in model["features"]] for w in words])
        for w, r in zip(words, model["estimator"].predict_proba(X)[:, 1]):
            w.risk = float(r)
    else:  # untrained fallback: simple rules
        for w in words:
            w.risk = min(1.0, 0.4 * (w.f["ctc"] < 0.05) + 0.3 * (w.f.get("asr_dt", 0) > 1.0)
                         + 0.3 * (w.f["free_dev"] > 1.0))
    for w in words:
        w.reasons = reasons(w.f)


def reasons(f: dict[str, float]) -> list[str]:
    """Human-readable evidence for the editor."""
    out = []
    if f.get("asr_line_heard", 1.0) < 0.2 and f.get("asr_none", 0):
        out.append("Whisper did not hear this line")
    elif f.get("asr_dt", 0) >= 1.0 and not f.get("asr_none", 0):
        out.append(f"Whisper heard this word {f['asr_dt']:.1f} s away")
    if f.get("free_dev", 0) > 1.0:
        out.append("audio alignment disagrees with the lyrics timing")
    if f.get("has_times") and f.get("lrc_dev", 0) > 2.0:
        out.append(f"{f['lrc_dev']:.1f} s from the downloaded timing")
    if f.get("ctc", 1) < 0.05:
        out.append("word hard to recognise in the vocals")
    if f.get("voiced", 1) < 0.2:
        out.append("little singing detected here")
    if f.get("repeat"):
        out.append("repeat added from what was heard")
    if f.get("near_leftover"):
        out.append("sung words nearby are missing from the lyrics")
    return out


def line_issues(words: list[WordInfo], threshold: float, leftovers: list[tuple[float, float, str]] = ()) -> list[dict]:
    """Group risky words into one issue per screen line (+ 'missing lyrics' regions)."""
    by_line: dict[int, list[WordInfo]] = {}
    for w in words:
        by_line.setdefault(w.line, []).append(w)
    issues = []
    for li, ws in sorted(by_line.items()):
        risk = line_risk(ws)
        if risk < threshold:
            continue
        bad = [w for w in ws if w.risk >= threshold] or ws
        why = reasons(_aggregate([w.f for w in bad]))
        issues.append({"kind": "timing", "line": li, "start": round(ws[0].start, 3),
                       "end": round(ws[-1].end, 3), "risk": round(risk, 3),
                       "text": " ".join(w.text for w in ws),
                       "words": [round(w.risk, 3) for w in ws], "reasons": why[:3]})
    for a, b, text in leftovers:
        issues.append({"kind": "missing_lyrics", "line": None, "start": round(a, 3), "end": round(b, 3),
                       "risk": 0.9, "text": text, "reasons": ["Whisper heard words that are not in the lyrics"]})
    issues.sort(key=lambda x: -x["risk"])
    return issues


_MIN_KEYS = {"ctc", "voiced", "asr_line_heard"}


def _aggregate(fs: list[dict[str, float]]) -> dict[str, float]:
    """Line-level evidence from its risky words: worst value per feature."""
    keys = set().union(*fs)
    return {k: (min if k in _MIN_KEYS else max)(f.get(k, 0.0) for f in fs) for k in keys}


def line_risk(ws: list[WordInfo]) -> float:
    """A line needs review if a good part of it is risky (one shaky word is normal)."""
    rs = sorted((w.risk for w in ws), reverse=True)
    k = max(1, math.ceil(len(rs) / 3))
    return sum(rs[:k]) / k


def to_json(words: list[WordInfo]) -> list[dict]:
    return [{"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3), "line": w.line,
             "src_line": w.src_line, "risk": round(w.risk, 4),
             "f": {k: round(v, 4) for k, v in w.f.items()}} for w in words]


# ---- double-check consensus auto-correct (roadmap B3, PR-10) -----------------
#
# When more than one pitch double-check model is configured (ULTRAAI_PITCH_CHECK) and at
# least two of them agree with each other on a sung note while disagreeing with the main
# pitch model, the note's pitch is corrected to their consensus.  Called from
# generate.double_check_issues with per-model syllable pitches already octave-folded
# next to the main pitch (generate._fold).

AUTOFIX_AGREE_SEMITONES = 1.0    # two check models "agree" when their octave-folded
                                 # pitches for the note are within this many semitones
AUTOFIX_MIN_OFF_SEMITONES = 2.0  # the consensus must differ from the main pitch by at
                                 # least this many semitones (octave-folded) to correct
AUTOFIX_MIN_MODELS = 2           # need >= this many check models configured, and >= this
                                 # many of them agreeing, before auto-correcting


def consensus_autofix(main_pitch: float, check_pitches: dict[str, float],
                      agree_tol: float = AUTOFIX_AGREE_SEMITONES,
                      min_off: float = AUTOFIX_MIN_OFF_SEMITONES,
                      min_models: int = AUTOFIX_MIN_MODELS) -> tuple[float, list[str]] | None:
    """Consensus of the double-check models for one sung note.

    ``check_pitches`` maps each double-check model to its pitch for the note, already
    octave-folded next to ``main_pitch``.  Returns ``(consensus_pitch, agreeing_models)``
    when all of these hold:

      * at least ``min_models`` check models are configured,
      * at least ``min_models`` of them agree with each other within ``agree_tol``
        semitones (the largest such group wins; ties go to the lower-pitched group),
      * their median (the consensus) differs from ``main_pitch`` by at least ``min_off``
        semitones.

    Otherwise returns ``None``: the main pitch stands and the caller keeps the plain
    disagreement issue.  The corrected pitch is the consensus rounded to the nearest
    semitone (done by the caller).
    """
    if len(check_pitches) < min_models:
        return None
    items = sorted(check_pitches.items(), key=lambda kv: kv[1])
    vals = [v for _, v in items]
    lo = 0
    best_lo, best_hi, best_n = 0, 0, 1
    for hi in range(len(vals)):
        while vals[hi] - vals[lo] > agree_tol:  # window [lo..hi] within agree_tol of each other
            lo += 1
        if hi - lo + 1 > best_n:
            best_lo, best_hi, best_n = lo, hi, hi - lo + 1
    if best_n < min_models:
        return None
    group = items[best_lo:best_hi + 1]
    consensus = float(statistics.median([v for _, v in group]))
    if abs(consensus - main_pitch) < min_off:
        return None
    return consensus, [m for m, _ in group]


def autofix_issue(s, models: list[str], old_pitch: int, new_pitch: int, main_name: str,
                  risk: float = 0.25) -> dict:
    """Sidecar entry for a note whose pitch was auto-corrected by the check consensus
    (kind ``auto-fixed``; ``s`` is the generate.Syllable, duck-typed)."""
    return {"kind": "auto-fixed", "line": s.line, "start": round(s.start, 3), "end": round(s.end, 3),
            "risk": round(risk, 3), "text": s.text.strip(), "models": list(models),
            "old_pitch": old_pitch, "new_pitch": new_pitch,
            "reasons": [f"pitch auto-corrected to the consensus of {', '.join(models)}: "
                        f"{old_pitch} -> {new_pitch} MIDI ({main_name} said {old_pitch})"]}


# Song-level check (184 library songs, BENCHMARK.md "Lyrics warning"): songs where Whisper heard
# < 40 % of the lyrics, or where >= half the lines are uncertain, are much worse on average
# (57 % of words timed right vs 80 %); the rule flags 17 % of songs and 18 of the 29 worst.
SONG_HEARD_MIN = 0.4
SONG_UNSURE_SHARE = 0.5


def song_check(info: dict, start: float = 0.0) -> dict | None:
    """One song-level issue when the whole song needs a careful look, else None."""
    reasons = []
    heard = info.get("heard")
    if heard is not None and heard < SONG_HEARD_MIN:
        reasons.append(f"Only {round(100 * heard)} % of the lyrics were heard in the singing – they may be "
                       "another version, the wrong song, or partly missing")
    lines = info.get("lines") or []
    if lines:
        share = info.get("low_confidence_lines", 0) / len(lines)
        if share >= SONG_UNSURE_SHARE:
            reasons.append(f"{round(100 * share)} % of the lines are uncertain")
    if not reasons:
        return None
    return {"kind": "song_check", "line": None, "start": round(start, 3), "end": round(start + 0.5, 3),
            "risk": 0.95, "text": "Check this song carefully" if heard is None or heard >= SONG_HEARD_MIN
            else "Lyrics may not match the singing", "reasons": reasons}
