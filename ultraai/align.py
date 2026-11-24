"""Forced alignment of known lyrics to a (separated) vocal track.

Uses the multilingual MMS_FA wav2vec2 CTC model from torchaudio (romanised a-z alphabet,
plus a <star> token that can absorb audio not covered by the transcript, e.g. ad-libs).
Long songs are processed in overlapping windows; the Viterbi alignment runs on the
whole song at once so every word keeps its order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import config
from .lyrics import Lyrics  # torch is imported lazily: the Studio base install has no torch

SR = 16000
HOP = 320                 # samples per emission frame (20 ms)
FRAME_S = HOP / SR
WINDOW_S = 30.0
CONTEXT_S = 2.0

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from torchaudio.pipelines import MMS_FA

        model = MMS_FA.get_model(with_star=True)
        model.eval()
        _MODEL = (model, MMS_FA.get_dict(star="*"))
    return _MODEL


def emissions(wave: np.ndarray) -> "torch.Tensor":
    """Log-probabilities [T, C] for a mono 16 kHz waveform, computed window by window."""

    import torch

    with torch.inference_mode():
        return _emissions_impl(wave, _model()[0])


def _emissions_impl(wave: np.ndarray, model) -> "torch.Tensor":
    import torch

    total = len(wave)
    n_frames = total // HOP
    win, ctx = int(WINDOW_S * SR), int(CONTEXT_S * SR)
    out: list[torch.Tensor] = []
    pos = 0
    while pos < total:
        a, b = max(0, pos - ctx), min(total, pos + win + ctx)
        chunk = torch.from_numpy(wave[a:b]).float().unsqueeze(0)
        em, _ = model(chunk)
        em = torch.log_softmax(em[0], dim=-1)
        first = (pos - a) // HOP
        count = min(win, total - pos) // HOP
        out.append(em[first: first + count])
        pos += win
    em = torch.cat(out)
    if len(em) < n_frames:  # pad tail rounding
        em = torch.cat([em, em[-1:].expand(n_frames - len(em), -1)])
    return em[:n_frames]


@dataclass
class CharTime:
    char: str
    start: float
    end: float
    score: float


@dataclass
class AlignedWord:
    index: int                         # index into Lyrics.words
    chars: list[CharTime]
    syllable_starts: list[float]       # one per syllable
    start: float
    end: float
    score: float                       # mean char probability (0..1)


def _align_span(lyrics: Lyrics, indices: list[int], em: torch.Tensor, frame0: int,
                star_between: str) -> list[AlignedWord]:
    """Viterbi-align ``lyrics.words[indices]`` to emission frames ``em`` (starting at ``frame0``)."""
    import warnings

    import torch
    from torchaudio.functional import forced_align, merge_tokens

    _, dictionary = _model()
    star = dictionary["*"]
    targets: list[int] = [star]
    owners: list[tuple[int, int] | None] = [None]  # (word index, char index) per target token
    prev = None
    for wi in indices:
        w = lyrics.words[wi]
        if prev is not None and (star_between == "words" or
                                 (star_between == "lines" and lyrics.words[prev].line != w.line)):
            targets.append(star)
            owners.append(None)
        for ci, ch in enumerate(w.align):
            targets.append(dictionary[ch])
            owners.append((wi, ci))
        prev = wi
    targets.append(star)
    owners.append(None)
    if len(em) < len(targets) * 2:  # window too small to hold the words
        raise ValueError("window too short")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ali, scores = forced_align(em.unsqueeze(0), torch.tensor([targets], dtype=torch.int32), blank=0)
    spans = merge_tokens(ali[0], scores[0].exp())
    assert len(spans) == len(targets), (len(spans), len(targets))

    per_word: dict[int, list[CharTime]] = {}
    for span, owner in zip(spans, owners):
        if owner is None:
            continue
        wi, ci = owner
        per_word.setdefault(wi, []).append(CharTime(lyrics.words[wi].align[ci], (frame0 + span.start) * FRAME_S,
                                                    (frame0 + span.end) * FRAME_S, float(span.score)))
    result = []
    for wi in indices:
        w = lyrics.words[wi]
        chars = per_word[wi]
        starts, k = [], 0
        for syl in w.syllable_align:
            starts.append(chars[min(k, len(chars) - 1)].start)
            k += len(syl)
        result.append(AlignedWord(wi, chars, starts, chars[0].start, chars[-1].end,
                                  float(np.mean([c.score for c in chars]))))
    return result


def align(lyrics: Lyrics, wave: np.ndarray, star_between: str = "lines",
          em: torch.Tensor | None = None) -> list[AlignedWord]:
    """Align all words in one global pass; ``star_between`` = "lines" | "words" | "none"."""
    if em is None:
        em = emissions(wave)
    return _align_span(lyrics, list(range(len(lyrics.words))), em, 0, star_between)


# ------------------------------------------------------------------ robust alignment
ANCHOR_MIN_CONF = 0.15     # mean word confidence of a line to trust it
MAX_WORD_S = 4.0           # a single word longer than this is implausible
MARGIN_S = 0.3


def _line_ok(words: list[AlignedWord]) -> bool:
    conf = float(np.mean([w.score for w in words]))
    longest = max(w.end - w.start for w in words)
    return conf >= ANCHOR_MIN_CONF and longest <= MAX_WORD_S


def align_robust(lyrics: Lyrics, wave: np.ndarray, em: torch.Tensor | None = None,
                 passes: int = 2) -> list[AlignedWord]:
    """Global alignment, then re-align runs of untrusted lines between trusted anchor lines.

    Inside a run the aligner may skip non-lyric audio between every word (backing vocals,
    ad-libs), and it is confined to the gap between the surrounding anchors, so one bad
    stretch can no longer drag the words around it.
    """
    if em is None:
        em = emissions(wave)
    words = align(lyrics, wave, "lines", em)
    n_lines = max(w.line for w in lyrics.words) + 1
    for _ in range(passes):
        by_line: dict[int, list[AlignedWord]] = {}
        for w in words:
            by_line.setdefault(lyrics.words[w.index].line, []).append(w)
        ok = {li: _line_ok(ws) for li, ws in by_line.items()}
        changed = False
        li = 0
        while li < n_lines:
            if ok.get(li, True):
                li += 1
                continue
            run_start = li
            while li < n_lines and not ok.get(li, True):
                li += 1
            run = range(run_start, li)
            prev_end = by_line[run_start - 1][-1].end if run_start > 0 else 0.0
            next_start = by_line[li][0].start if li < n_lines else len(em) * FRAME_S
            f0 = max(0, int((prev_end + 0.0) / FRAME_S))
            f1 = min(len(em), int((next_start + MARGIN_S) / FRAME_S))
            indices = [w.index for r in run for w in by_line[r]]
            try:
                new = _align_span(lyrics, indices, em[f0:f1], f0, "words")
            except ValueError:
                continue
            old_conf = np.mean([w.score for r in run for w in by_line[r]])
            new_conf = np.mean([w.score for w in new])
            if new_conf >= old_conf * 0.9:
                lookup = {w.index: w for w in new}
                words = [lookup.get(w.index, w) for w in words]
                changed = True
        if not changed:
            break
    return words


# ------------------------------------------------------------------ Whisper time priors
ASR_PRIOR_WIN_S = 2.0   # only ASR words this close (s) to a line's anchor can center a word


def match_asr_time(text: str, asr_words: list, ref: float,
                   win: float = ASR_PRIOR_WIN_S) -> float:
    """Start time of the ASR word best matching lyric word ``text`` near ``ref`` seconds.

    Candidates are ASR words whose normalized-key similarity to ``text`` reaches
    ``issues.ASR_SIM`` (0.6) and whose start is within ``win`` s of ``ref``; the nearest
    in time wins.  Returns NaN when no ASR word matches.
    """
    from .issues import ASR_SIM, _key, _sim  # small matcher, same normalization as the review features

    k = _key(text)
    if not k:
        return float("nan")
    best, best_dt = float("nan"), float("inf")
    for w in asr_words:
        ak = _key(w.text)
        if not ak or _sim(k, ak) < ASR_SIM:
            continue
        dt = abs(w.start - ref)
        if dt <= win and dt < best_dt:
            best, best_dt = float(w.start), dt
    return best


# ------------------------------------------------------------------ LRC-anchored alignment
def viterbi(em: np.ndarray, targets: list[int], lo: np.ndarray, hi: np.ndarray,
            blank: int = 0, prior_centers: np.ndarray | None = None) -> list[tuple[int, int, float]]:
    """CTC forced alignment where target k may only occupy frames [lo[k], hi[k]).

    ``prior_centers``: optional per-target time prior in seconds (NaN = none).  When given
    and ``config.asr_prior`` (beta) > 0, each label state gets a Gaussian bonus
    ``beta * exp(-((t*FRAME_S - c_k)^2) / (2*sigma^2))`` (sigma = ``config.asr_prior_sigma``)
    added to its emission score, pulling the word toward the time Whisper heard it.

    Returns one (start_frame, end_frame, mean_prob) per target token.
    """
    T = em.shape[0]
    L = len(targets)
    S = 2 * L + 1
    ext = np.full(S, blank, dtype=np.int64)
    ext[1::2] = targets
    s_lo = np.zeros(S, dtype=np.int64)
    s_hi = np.full(S, T, dtype=np.int64)
    s_lo[1::2], s_hi[1::2] = lo, hi
    # a blank may not be used before the previous label is allowed / after the next one ends
    s_lo[2::2] = np.minimum(lo, T)                    # blank after label k
    s_hi[0:-1:2] = np.maximum(hi, 0)                  # blank before label k
    s_lo[0], s_hi[-1] = 0, T
    can_skip = np.zeros(S, dtype=bool)
    can_skip[3::2] = ext[3::2] != ext[1:-2:2]

    # precompute the prior bonus B[t, k] for the label states (odd indices; blanks get 0)
    prior_bonus: np.ndarray | None = None
    if prior_centers is not None:
        beta = config.get_float("asr_prior")
        sigma = config.get_float("asr_prior_sigma")
        centers = np.asarray(prior_centers, dtype=np.float64)
        if len(centers) != L:
            raise ValueError(f"prior_centers has {len(centers)} entries, expected {L}")
        if beta > 0 and sigma > 0:
            t_sec = np.arange(T, dtype=np.float64) * FRAME_S
            prior_bonus = np.zeros((T, L))
            for k in range(L):
                c = centers[k]
                if not np.isnan(c):
                    d = t_sec - c
                    prior_bonus[:, k] = beta * np.exp(-(d * d) / (2.0 * sigma * sigma))

    NEG = -1e30
    prev = np.full(S, NEG)
    bp = np.zeros((T, S), dtype=np.int8)
    frames = np.arange(S)
    for t in range(T):
        e = em[t, ext]
        if prior_bonus is not None:
            e[1::2] += prior_bonus[t]  # e is a fancy-indexed copy; label states are the odd entries
        allowed = (s_lo <= t) & (t < s_hi)
        if t == 0:
            cur = np.full(S, NEG)
            cur[:2] = e[:2]
        else:
            stay = prev
            step = np.concatenate(([NEG], prev[:-1]))
            skip = np.where(can_skip, np.concatenate(([NEG, NEG], prev[:-2])), NEG)
            best = np.maximum(stay, np.maximum(step, skip))
            choice = np.where(best == stay, 0, np.where(best == step, 1, 2)).astype(np.int8)
            bp[t] = choice
            cur = best + e
        cur[~allowed] = NEG
        prev = cur
    s = S - 1 if prev[S - 1] >= prev[S - 2] else S - 2
    if prev[s] <= NEG / 2:
        raise ValueError("no path satisfies the time constraints")
    path = np.empty(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])
    del frames
    out: list[tuple[int, int, float]] = []
    probs = np.exp(em[np.arange(T), ext[path]])
    for k in range(L):
        idx = np.nonzero(path == 2 * k + 1)[0]
        out.append((int(idx[0]), int(idx[-1]) + 1, float(probs[idx].mean())))
    return out


ANCHOR_WINDOW_S = 1.5


def line_starts(lyrics: Lyrics, words: list[AlignedWord]) -> dict[int, tuple[float, float]]:
    """line -> (start time, mean confidence)."""
    out: dict[int, list[AlignedWord]] = {}
    for w in words:
        out.setdefault(lyrics.words[w.index].line, []).append(w)
    return {li: (ws[0].start, float(np.mean([w.score for w in ws]))) for li, ws in out.items()}


def lrc_offsets(lyrics: Lyrics, words: list[AlignedWord], times: list[float],
                min_conf: float = 0.15, radius: int = 4) -> list[float] | None:
    """Per-line offset (aligned - LRC) from confident lines, smoothed locally.

    Returns None when the LRC timing does not fit this audio (e.g. a different edit).
    """
    starts = line_starts(lyrics, words)
    d = {li: s - times[li] for li, (s, c) in starts.items() if c >= min_conf}
    if len(d) < 4:
        return None
    glob = float(np.median(list(d.values())))
    fit = np.mean([abs(v - glob) < ANCHOR_WINDOW_S for v in d.values()])
    out = []
    for li in range(len(times)):
        near = [v for j, v in d.items() if abs(j - li) <= radius]
        local = float(np.median(near)) if len(near) >= 3 else glob
        out.append(local)
    # accept if the locally-corrected LRC explains most confident lines
    local_fit = np.mean([abs(v - out[j]) < ANCHOR_WINDOW_S for j, v in d.items()])
    if max(fit, local_fit) < 0.6:
        return None
    return out


def repair_anchors(lyrics: Lyrics, words: list[AlignedWord], times: list[float], offsets: list[float],
                   min_conf: float = 0.05, window: float = ANCHOR_WINDOW_S) -> tuple[list[float], list[int]]:
    """Replace LRC line times that the audio clearly contradicts.

    Community LRC files sometimes carry a wrong timestamp (e.g. a line stamped before an
    18 s instrumental break instead of after it).  When the free alignment places a line
    reasonably confidently somewhere else, trust the audio for that line.
    Returns (new LRC times, indices of repaired lines).
    """
    starts = line_starts(lyrics, words)
    new, fixed = list(times), []
    for li, t in enumerate(times):
        if li not in starts:
            continue
        s, c = starts[li]
        if c >= min_conf and abs(s - (t + offsets[li])) > window:
            new[li] = s - offsets[li]
            fixed.append(li)
    # keep anchors monotonic: a repaired line may not jump past its neighbours
    for li in range(1, len(new)):
        if new[li] < new[li - 1] and li in fixed:
            new[li] = new[li - 1]
    return new, fixed


def align_anchored(lyrics: Lyrics, em: torch.Tensor, times: list[float],
                   offsets: list[float], window: float = ANCHOR_WINDOW_S,
                   asr_words: list | None = None) -> list[AlignedWord]:
    """Global alignment with each line confined to [t_i - window, t_{i+1} + window].

    ``asr_words``: Whisper word times (start/end/text/prob).  When given, the first char
    token of each lyric word gets a time prior centered on the best-matching ASR word near
    its line's anchor (see :func:`match_asr_time`); ``viterbi`` adds the Gaussian bonus.
    """
    _, dictionary = _model()
    star = dictionary["*"]
    em_np = em.numpy()
    T = len(em_np)
    anchor = [t + o for t, o in zip(times, offsets)]
    targets, owners, lo, hi = [star], [None], [0], [T]
    prior_centers: list[float] | None = [float("nan")] if asr_words else None
    prev_line = None
    for wi, w in enumerate(lyrics.words):
        li = w.line
        if prev_line is not None and li != prev_line:
            targets.append(star); owners.append(None); lo.append(0); hi.append(T)  # noqa: E702
            if prior_centers is not None:
                prior_centers.append(float("nan"))
        a = max(0, int((anchor[li] - window) / FRAME_S))
        nxt = anchor[li + 1] if li + 1 < len(anchor) else T * FRAME_S
        b = min(T, int((max(nxt, anchor[li]) + window) / FRAME_S))
        for ci, ch in enumerate(w.align):
            targets.append(dictionary[ch]); owners.append((wi, ci)); lo.append(a); hi.append(b)  # noqa: E702
            if prior_centers is not None:
                # prior on the word's first char token, centered on the matching ASR word
                prior_centers.append(match_asr_time(w.display, asr_words, anchor[li]) if ci == 0
                                     else float("nan"))
        prev_line = li
    targets.append(star); owners.append(None); lo.append(0); hi.append(T)  # noqa: E702
    if prior_centers is not None:
        prior_centers.append(float("nan"))
    spans = viterbi(em_np, targets, np.array(lo), np.array(hi),
                    prior_centers=np.array(prior_centers) if prior_centers is not None else None)

    per_word: dict[int, list[CharTime]] = {}
    for (s, e, p), owner in zip(spans, owners):
        if owner is None:
            continue
        wi, ci = owner
        per_word.setdefault(wi, []).append(CharTime(lyrics.words[wi].align[ci], s * FRAME_S, e * FRAME_S, p))
    result = []
    for wi, w in enumerate(lyrics.words):
        chars = per_word[wi]
        starts, k = [], 0
        for syl in w.syllable_align:
            starts.append(chars[min(k, len(chars) - 1)].start)
            k += len(syl)
        result.append(AlignedWord(wi, chars, starts, chars[0].start, chars[-1].end,
                                  float(np.mean([c.score for c in chars]))))
    return result
