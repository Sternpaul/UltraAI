"""The constrained CTC viterbi and the LRC-anchor helpers in ultraai/align.py (pure numpy)."""

import numpy as np
import pytest

from ultraai.align import lrc_offsets, repair_anchors, viterbi
from ultraai.lyrics import prepare


def _em(T, C, peaks):
    """Emission matrix: -10 everywhere, -0.1 for blank, -0.01 where a token peaks.

    ``peaks``: {token: (first_frame, last_frame_exclusive)}; token 0 is the blank.
    """
    em = np.full((T, C), -10.0)
    em[:, 0] = -0.1
    for tok, (a, b) in peaks.items():
        em[a:b, tok] = -0.01
    return em


def test_viterbi_known_path():
    em = _em(8, 4, {1: (0, 2), 2: (2, 4), 3: (4, 6)})
    em[6:8, 0] = -0.01                                    # trailing blank
    spans = viterbi(em, [1, 2, 3], np.zeros(3, dtype=np.int64), np.full(3, 8, dtype=np.int64))
    assert [(s, e) for s, e, _ in spans] == [(0, 2), (2, 4), (4, 6)]
    assert spans[0][2] == pytest.approx(np.exp(-0.01))    # mean prob of the span


def test_viterbi_constraints_force_a_different_path():
    em = _em(8, 4, {1: (0, 6), 2: (6, 8)})                 # token 1 naturally spans frames 0..5
    free = viterbi(em, [1, 2], np.zeros(2, dtype=np.int64), np.full(2, 8, dtype=np.int64))
    assert (free[0][0], free[0][1]) == (0, 6)
    # confine token 1 to frames [4, 6): the path must move there instead
    forced = viterbi(em, [1, 2], np.array([4, 0]), np.array([6, 8]))
    assert (forced[0][0], forced[0][1]) == (4, 6)
    assert (forced[1][0], forced[1][1]) == (6, 8)
    for (s, e, _), lo, hi in zip(forced, [4, 0], [6, 8]):
        assert lo <= s and e <= hi                         # every span inside its window


def test_viterbi_no_path_raises_value_error():
    em = _em(8, 4, {1: (0, 8)})
    with pytest.raises(ValueError, match="no path"):
        viterbi(em, [1], np.array([5]), np.array([5]))        # empty interval [5, 5)
    with pytest.raises(ValueError, match="no path"):
        viterbi(em, [1, 2], np.array([6, 0]), np.array([2, 8]))  # lo > hi for target 0


def _lyr6():
    return prepare(["one", "two", "three", "four", "five", "six"], "en")


def test_lrc_offsets_constant_shift(make_aligned):
    lyr = _lyr6()
    times = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    words = make_aligned(lyr, [t + 2.0 for t in times])     # audio runs 2 s after the LRC
    assert lrc_offsets(lyr, words, times) == pytest.approx([2.0] * 6)


def test_lrc_offsets_too_few_confident_lines(make_aligned):
    lyr = _lyr6()
    times = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    words = make_aligned(lyr, [t + 2.0 for t in times], score=0.01)   # below min_conf
    assert lrc_offsets(lyr, words, times) is None


def test_lrc_offsets_rejects_mismatched_lrc(make_aligned):
    lyr = _lyr6()
    times = [0.0, 2.0, 4.0, 50.0, 52.0, 54.0]              # an LRC of a different edit
    words = make_aligned(lyr, [0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
    assert lrc_offsets(lyr, words, times) is None


def test_repair_anchors_replaces_contradicted_line(make_aligned):
    lyr = prepare(["one", "two", "three", "four"], "en")
    times = [0.0, 2.0, 4.0, 6.0]
    words = make_aligned(lyr, [0.0, 2.0, 20.0, 6.0])        # line 2 stamped 16 s too early
    new, fixed = repair_anchors(lyr, words, times, [0.0] * 4)
    assert fixed == [2]
    assert new[2] == pytest.approx(20.0)
    assert new[:2] == times[:2] and new[3] == times[3]


def test_repair_anchors_keeps_monotonic(make_aligned):
    lyr = prepare(["one", "two", "three", "four"], "en")
    times = [0.0, 2.0, 4.0, 6.0]
    words = make_aligned(lyr, [0.0, 30.0, 28.0, 6.0])       # repaired lines out of order
    new, fixed = repair_anchors(lyr, words, times, [0.0] * 4)
    assert fixed == [1, 2]
    assert new[2] == pytest.approx(new[1])                 # clamped up to line 1


def test_repair_anchors_ignores_low_confidence(make_aligned):
    lyr = prepare(["one", "two", "three", "four"], "en")
    times = [0.0, 2.0, 4.0, 6.0]
    words = make_aligned(lyr, [0.0, 2.0, 20.0, 6.0], score=0.01)
    new, fixed = repair_anchors(lyr, words, times, [0.0] * 4)
    assert fixed == [] and new == times
