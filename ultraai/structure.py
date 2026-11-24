"""What is actually sung: line structure from a speech transcript + downloaded lyrics.

Downloaded lyrics often write a chorus once where it is sung twice, drop a line, or carry
wrong timestamps.  We align the Whisper transcript (words with rough times) to the lyric
lines with a dynamic program that walks through the lyrics word by word and may, at any
line boundary, *jump* to the start of any line (a repeat, or skipping ahead).  The result
is the *sung script*: lyric lines (official spelling) in sung order, with times taken from
the transcript.

Costs (log-odds style; tuned on the benchmark):
  match     2*similarity - 1          (similar words reward, different words cost)
  insert    transcript word not in the lyrics (ad-lib, hallucination)
  delete    lyric word the transcript missed (quiet, backing, ASR gap) - cheap: ASR misses a lot
  jump      go to another line than the next one
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import config
from .lyrics import clean_lines
from .transcribe import AsrSegment

INSERT = -0.6
DELETE = -0.1      # Whisper misses a lot of sung words: not hearing a line is no evidence against it
JUMP = -3.0        # only backwards (a repeat); skipping ahead happens via (cheap) deletions
MIN_SIM = 0.5


def _key(word: str) -> str:
    from .eval.metrics import norm_token

    return norm_token(word)


@dataclass
class SungLine:
    line: int                     # index into the lyric lines
    text: str
    start: float | None           # from the transcript (first matched word); None if unheard
    end: float | None
    matched: int                  # lyric words matched to transcript words
    words: int


@dataclass
class Structure:
    lines: list[SungLine]
    score: float                  # total DP score
    coverage: float               # matched lyric words / transcript words
    jumps: int                    # non-sequential line transitions
    asr_words: int
    lyric_lines: list[str] = field(default_factory=list)

    @property
    def per_word(self) -> float:
        return self.score / max(1, self.asr_words)


def _sim_matrix(a: list[str], b: list[str]) -> list[list[float]]:
    cache: dict[tuple[str, str], float] = {}
    out = []
    for x in a:
        row = []
        for y in b:
            k = (x, y)
            if k not in cache:
                if x == y:
                    s = 1.0
                elif not x or not y:
                    s = 0.0
                else:
                    s = SequenceMatcher(None, x, y, autojunk=False).ratio()
                cache[k] = s
            row.append(cache[k])
        out.append(row)
    return out


TIME_TOL = 3.0     # s: LRC line times are this rough before a penalty starts
TIME_RATE = 0.25   # penalty per second beyond the tolerance ...
TIME_CAP = 1.0     # ... capped, so a far-away repeat can still match


def match(lyric_lines: list[str], asr: list[AsrSegment], line_times: list[float] | None = None,
          offset: float = 0.0) -> Structure:
    """Align transcript words to lyric lines allowing repeats/skips (see module doc).

    ``line_times`` (synced lyrics, shifted by ``offset`` into transcript time): matching a
    word far from its line's time costs extra, so a word sung twice is given to the nearer
    occurrence of that text instead of the first one."""
    lw: list[str] = []          # lyric word keys, flattened
    line_of: list[int] = []
    starts: list[int] = []      # flattened index of each line's first word
    for li, line in enumerate(lyric_lines):
        starts.append(len(lw))
        for tok in line.split():
            k = _key(tok)
            if k:
                lw.append(k)
                line_of.append(li)
    M = len(lw)
    boundary = sorted(set(starts + [M]))
    start_set = set(starts)
    aw = [(w.start, w.end, _key(w.text)) for s in asr for w in s.words]
    aw = [w for w in aw if w[2]]
    N = len(aw)
    if not M or not N:
        return Structure([], 0.0, 0.0, 0, N, lyric_lines)
    sim = _sim_matrix([w[2] for w in aw], lw)
    pen = None
    if line_times is not None:
        lt = [line_times[line_of[q]] + offset for q in range(M)]
        pen = [[min(TIME_CAP, TIME_RATE * max(0.0, abs(t0 - lt[q]) - TIME_TOL)) for q in range(M)]
               for t0, _, _ in aw]

    NEG = -1e18
    # V[p]: best score with p = index of the next lyric word to sing; bp[n][p] = (kind, prev p)
    V = [NEG] * (M + 1)
    V[0] = 0.0
    bps: list[list[tuple[str, int]]] = []

    def epsilon(V, bp):
        # deletions (skip lyric word p) sweep forward
        for p in range(1, M + 1):
            v = V[p - 1] + DELETE
            if v > V[p]:
                V[p], bp[p] = v, ("del", p - 1)
        # backward jumps (repeats): from the best boundary after s to the start s
        best_b, best_v = -1, NEG
        for b in reversed(boundary):
            if b in start_set and best_b >= 0:
                vb = best_v + JUMP
                if vb > V[b]:
                    V[b], bp[b] = vb, ("jump", best_b)
            if V[b] > best_v and b > 0:
                best_b, best_v = b, V[b]

    bp0: list[tuple[str, int]] = [("start", -1)] * (M + 1)
    epsilon(V, bp0)
    bps.append(bp0)
    for n in range(N):
        W = [NEG] * (M + 1)
        bp: list[tuple[str, int]] = [("none", -1)] * (M + 1)
        row = sim[n]
        prow = pen[n] if pen is not None else None
        for p in range(M + 1):
            v = V[p] + INSERT
            if v > W[p]:
                W[p], bp[p] = v, ("ins", p)
            if p < M:
                s = row[p]
                v = V[p] + (2 * s - 1 if s >= MIN_SIM else -1.5) - (prow[p] if prow else 0.0)
                if v > W[p + 1]:
                    W[p + 1], bp[p + 1] = v, ("match", p)
        epsilon(W, bp)
        bps.append(bp)
        V = W

    # end anywhere, but unsung remaining words cost half a deletion each
    end_p = max(range(M + 1), key=lambda p: V[p] + 0.5 * DELETE * (M - p))
    score = V[end_p]

    # backtrace -> list of (kind, lyric word index, asr word index)
    events: list[tuple[str, int, int]] = []
    n, p = N, end_p
    while True:
        kind, q = bps[n][p]
        if kind == "start":
            break
        if kind == "match":
            events.append(("match", q, n - 1))
            n, p = n - 1, q
        elif kind == "ins":
            n, p = n - 1, q
        elif kind == "del":
            events.append(("del", q, -1))
            p = q
        elif kind == "jump":
            events.append(("jump", q, -1))
            p = q
        else:  # unreachable state
            break
    events.reverse()
    # lyrics after the last heard word are still sung (Whisper often stops early): keep them
    events.extend(("del", q, -1) for q in range(end_p, M))

    # group into sung line occurrences
    out: list[SungLine] = []
    cur: SungLine | None = None
    jumps = 0
    matched_total = 0
    for kind, q, ai in events:
        if kind == "jump":
            jumps += 1
            cur = None
            continue
        li = line_of[q]
        if cur is None or cur.line != li:
            cur = SungLine(li, lyric_lines[li], None, None, 0, 0)
            out.append(cur)
        cur.words += 1
        if kind == "match":
            cur.matched += 1
            matched_total += 1
            t0, t1, _ = aw[ai]
            cur.start = t0 if cur.start is None else min(cur.start, t0)
            cur.end = t1 if cur.end is None else max(cur.end, t1)
    out = _drop_impossible(out)
    return Structure(out, score, matched_total / N, jumps, N, lyric_lines)


SECONDS_PER_WORD_MIN = 0.2
def _drop_impossible(lines: list[SungLine]) -> list[SungLine]:
    """Unheard lines squeezed between two heard lines with no time for them are not sung here
    (e.g. a verse the radio edit cuts).  Unheard lines with room for them are kept."""
    out: list[SungLine] = []
    i = 0
    while i < len(lines):
        if lines[i].matched:
            out.append(lines[i])
            i += 1
            continue
        j = i
        while j < len(lines) and not lines[j].matched:
            j += 1
        run = lines[i:j]
        prev_end = out[-1].end if out else None
        next_start = lines[j].start if j < len(lines) else None
        words = sum(ln.words for ln in run)
        if config.get_bool("struct_drop") and prev_end is not None and next_start is not None \
                and next_start - prev_end < SECONDS_PER_WORD_MIN * words:
            pass  # no room: drop the run
        else:
            out.extend(run)
        i = j
    return out


def lyric_lines_from_text(raw: str | list[str]) -> list[str]:
    return clean_lines(raw)


def fill_times(lines: list[SungLine], duration: float) -> list[float]:
    """Start time per sung line: transcript time, or interpolated between heard neighbours."""
    times: list[float | None] = [ln.start for ln in lines]
    known = [i for i, t in enumerate(times) if t is not None]
    if not known:
        return [duration * i / max(1, len(lines)) for i in range(len(lines))]
    out: list[float] = []
    for i, t in enumerate(times):
        if t is not None:
            out.append(t)
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is None:
            out.append(max(0.0, times[nxt] - 3.0 * (nxt - i)))
        elif nxt is None:
            out.append(min(duration, (lines[prev].end or times[prev]) + 3.0 * (i - prev)))
        else:
            a = lines[prev].end or times[prev]
            frac = (i - prev) / (nxt - prev)
            out.append(a + frac * (times[nxt] - a))
    return out


def describe(st: Structure) -> str:
    rows = [f"score/word {st.per_word:.2f}  coverage {st.coverage:.0%}  jumps {st.jumps}  lines {len(st.lines)}"]
    for ln in st.lines:
        t = f"{ln.start:7.2f}" if ln.start is not None else "      -"
        rows.append(f"{t}  L{ln.line:<3} {ln.matched:2}/{ln.words:<2} {ln.text}")
    return "\n".join(rows)
