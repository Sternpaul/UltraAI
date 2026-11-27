import math
import random
from difflib import SequenceMatcher

from ultraai.eval.metrics import TimedWord, match_words
from ultraai.structure import (
    DELETE,
    INSERT,
    JUMP,
    MIN_SIM,
    TIME_CAP,
    TIME_RATE,
    TIME_TOL,
    Structure,
    SungLine,
    _drop_impossible,
    _key,
    fill_times,
    match,
)
from ultraai.transcribe import AsrSegment, AsrWord


def asr(*lines_with_times):
    segs = []
    for t, text in lines_with_times:
        words = [AsrWord(t + 0.3 * i, t + 0.3 * i + 0.25, w, 0.9) for i, w in enumerate(text.split())]
        segs.append(AsrSegment(t, words[-1].end, text, words))
    return segs


LYRICS = ["one two three four", "five six seven eight", "chorus line goes here", "nine ten eleven twelve"]


def test_sequential():
    st = match(LYRICS, asr((1, "one two three four"), (4, "five six seven eight"),
                           (8, "chorus line goes here"), (12, "nine ten eleven twelve")))
    assert [ln.line for ln in st.lines] == [0, 1, 2, 3]
    assert st.jumps == 0
    assert abs(st.lines[2].start - 8) < 1e-6


def test_repeated_chorus_is_added():
    st = match(LYRICS, asr((1, "one two three four"), (4, "five six seven eight"),
                           (8, "chorus line goes here"), (10, "chorus line goes here"),
                           (14, "nine ten eleven twelve")))
    assert [ln.line for ln in st.lines] == [0, 1, 2, 2, 3]
    assert abs(st.lines[3].start - 10) < 1e-6


def test_unheard_lines_are_kept():
    st = match(LYRICS, asr((1, "one two three four"), (12, "nine ten eleven twelve")))
    assert [ln.line for ln in st.lines] == [0, 1, 2, 3]
    assert st.lines[1].start is None
    times = fill_times(st.lines, 20.0)
    assert times[0] < times[1] < times[2] < times[3]


def test_misspelled_transcript_still_matches():
    st = match(["I knew you were trouble when you walked in"],
               asr((5, "i new you where trubble when you walk in")))
    assert st.lines[0].matched >= 6


# ---------------------------------------------------------------------------
# DP rewrite (uint8 backpointers + rolling rows + unique-pair sim cache):
# the result must be bit-identical to the v0.1.17 implementation.
# ---------------------------------------------------------------------------

def _ref_sim_matrix(a, b):
    cache = {}
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


def _reference_match(lyric_lines, asr, line_times=None, offset=0.0):
    """Verbatim copy of structure.match() at v0.1.17 (origin/main): the bit-identical baseline."""
    lw, line_of, starts = [], [], []
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
    sim = _ref_sim_matrix([w[2] for w in aw], lw)
    pen = None
    if line_times is not None:
        lt = [line_times[line_of[q]] + offset for q in range(M)]
        pen = [[min(TIME_CAP, TIME_RATE * max(0.0, abs(t0 - lt[q]) - TIME_TOL)) for q in range(M)]
               for t0, _, _ in aw]

    NEG = -1e18
    V = [NEG] * (M + 1)
    V[0] = 0.0
    bps = []

    def epsilon(V, bp):
        for p in range(1, M + 1):
            v = V[p - 1] + DELETE
            if v > V[p]:
                V[p], bp[p] = v, ("del", p - 1)
        best_b, best_v = -1, NEG
        for b in reversed(boundary):
            if b in start_set and best_b >= 0:
                vb = best_v + JUMP
                if vb > V[b]:
                    V[b], bp[b] = vb, ("jump", best_b)
            if V[b] > best_v and b > 0:
                best_b, best_v = b, V[b]

    bp0 = [("start", -1)] * (M + 1)
    epsilon(V, bp0)
    bps.append(bp0)
    for n in range(N):
        W = [NEG] * (M + 1)
        bp = [("none", -1)] * (M + 1)
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

    end_p = max(range(M + 1), key=lambda p: V[p] + 0.5 * DELETE * (M - p))
    score = V[end_p]
    events = []
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
        else:
            break
    events.reverse()
    events.extend(("del", q, -1) for q in range(end_p, M))
    out = []
    cur = None
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


def _reference_match_words(ref, hyp, time_scale=5.0):
    """Verbatim copy of eval.metrics.match_words() at v0.1.17 (origin/main)."""
    n, m = len(ref), len(hyp)
    if not n or not m:
        return []
    timed = all(hasattr(w, "start") for w in ref) and all(hasattr(w, "start") for w in hyp)
    if not timed:
        sm = SequenceMatcher(None, [w.token for w in ref], [w.token for w in hyp], autojunk=False)
        return [(b.a + k, b.b + k) for b in sm.get_matching_blocks() for k in range(b.size)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]  # 0 diag, 1 up, 2 left
    for i in range(1, n + 1):
        ri, row, prev = ref[i - 1], score[i], score[i - 1]
        for j in range(1, m + 1):
            best, how = prev[j], 1
            if row[j - 1] > best:
                best, how = row[j - 1], 2
            hj = hyp[j - 1]
            if ri.token == hj.token:
                v = prev[j - 1] + 1.0 + 0.5 * math.exp(-abs(hj.start - ri.start) / time_scale)
                if v > best:
                    best, how = v, 0
            row[j], back[i][j] = best, how
    pairs, i, j = [], n, m
    while i and j:
        how = back[i][j]
        if how == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


VOCAB = ["love", "heart", "night", "star", "fire", "rain", "dance", "song", "blue", "sky",
         "dream", "light", "time", "road", "wind", "sea", "sun", "moon", "run", "fall",
         "hold", "stay", "know", "see", "way", "day", "eye", "hand", "home", "gone"]


def _rand_case(rng, max_lines=6, max_asr=14, timed=False):
    lines = [" ".join(rng.choice(VOCAB) for _ in range(rng.randint(1, 5)))
             for _ in range(rng.randint(1, max_lines))]
    words, t = [], 0.0
    for _ in range(rng.randint(1, max_asr)):
        w = rng.choice(VOCAB) if rng.random() < 0.7 else "xyzq"[: rng.randint(1, 4)]
        words.append(AsrWord(t, t + 0.25, w, 0.9))
        t += 0.3
    segs = [AsrSegment(0.0, t, " ".join(w.text for w in words), words)]
    line_times = None
    offset = 0.0
    if timed:
        line_times = [rng.uniform(0.0, 20.0) for _ in lines]
        offset = rng.choice([0.0, 0.4, -0.25])
    return lines, segs, line_times, offset


def test_identical_to_v017_reference_on_fixed_seeds():
    for seed in range(60):
        rng = random.Random(seed)
        lines, segs, lt, off = _rand_case(rng, timed=(seed % 2 == 0))
        got = match(lines, segs, line_times=lt, offset=off)
        want = _reference_match(lines, segs, line_times=lt, offset=off)
        assert got == want, f"seed {seed}: {got!r} != {want!r}"


def test_identical_to_v017_reference_larger_cases():
    for seed in range(60, 66):
        rng = random.Random(seed)
        lines, segs, lt, off = _rand_case(rng, max_lines=25, max_asr=90, timed=(seed % 2 == 0))
        assert match(lines, segs, line_times=lt, offset=off) == _reference_match(lines, segs, line_times=lt, offset=off)


def _brute_force_score(lyric_lines, asr, line_times=None, offset=0.0):
    """Independent reference for the DP score: relax the move graph (ins/match/del/jump
    edges) directly instead of running the epsilon sweeps, then pick the end state the
    same way.  Exact on tiny inputs (max-plus relaxation converges; del/jump cycles only
    lose score)."""
    lw, line_of, starts = [], [], []
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
        return 0.0
    sim = _ref_sim_matrix([w[2] for w in aw], lw)
    pen = None
    if line_times is not None:
        lt = [line_times[line_of[q]] + offset for q in range(M)]
        pen = [[min(TIME_CAP, TIME_RATE * max(0.0, abs(t0 - lt[q]) - TIME_TOL)) for q in range(M)]
               for t0, _, _ in aw]
    NEG = -1e18
    g = [[NEG] * (M + 1) for _ in range(N + 1)]
    g[0][0] = 0.0
    for _ in range((N + 1) * (M + 1)):  # enough passes for every simple path
        for n in range(N + 1):
            gn = g[n]
            for p in range(M + 1):
                v = gn[p]
                if v == NEG:
                    continue
                if n < N:  # ins / match consume asr word n
                    if v + INSERT > g[n + 1][p]:
                        g[n + 1][p] = v + INSERT
                    if p < M:
                        s = sim[n][p]
                        c = v + (2 * s - 1 if s >= MIN_SIM else -1.5)
                        if pen is not None:
                            c = c - pen[n][p]
                        if c > g[n + 1][p + 1]:
                            g[n + 1][p + 1] = c
                if p < M and v + DELETE > gn[p + 1]:  # del
                    gn[p + 1] = v + DELETE
                if p in boundary and p > 0:  # jump to any earlier line start
                    for s in start_set:
                        if s < p and v + JUMP > gn[s]:
                            gn[s] = v + JUMP
    end_p = max(range(M + 1), key=lambda p: g[N][p] + 0.5 * DELETE * (M - p))
    return g[N][end_p]


def test_score_matches_brute_force_on_tiny_random_cases():
    for seed in range(25):
        rng = random.Random(1000 + seed)
        lines, segs, lt, off = _rand_case(rng, max_lines=4, max_asr=8, timed=(seed % 3 == 0))
        st = match(lines, segs, line_times=lt, offset=off)
        assert st.score == _brute_force_score(lines, segs, line_times=lt, offset=off), f"seed {seed}"


def test_match_words_identical_to_v017_reference_on_fixed_seeds():
    for seed in range(30):
        rng = random.Random(2000 + seed)
        n, m = rng.randint(1, 15), rng.randint(1, 15)
        ref = [TimedWord(rng.choice(VOCAB[:12]), t, t + 0.2, [], [], 0)
               for t in sorted(rng.uniform(0, 30) for _ in range(n))]
        hyp = [TimedWord(rng.choice(VOCAB[:12]), t, t + 0.2, [], [], 0)
               for t in sorted(rng.uniform(0, 30) for _ in range(m))]
        assert match_words(ref, hyp) == _reference_match_words(ref, hyp), f"seed {seed}"
