"""Whisper word-time priors in the anchored Viterbi (PR-09 / roadmap B2).

Synthetic emissions (no torch/model needed): token 0 = blank, 1 = 'a', 2 = 'b'.
Word 'a' is acoustically best at 0.4-0.8 s (region A) with a slightly weaker
alternative at 2.4-2.8 s (region B); word 'b' lives at 4.0-4.4 s.  A strong prior
centered at 2.6 s must pull word 'a' from A to B.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from ultraai import config
from ultraai.align import FRAME_S, match_asr_time, viterbi

T = 300  # frames = 6 s


def synth_em() -> np.ndarray:
    em = np.empty((T, 3))
    em[:, 0] = np.log(0.40)   # blank baseline
    em[:, 1] = np.log(0.20)   # label 'a' baseline
    em[:, 2] = np.log(0.20)   # label 'b' baseline

    def bump(a: int, b: int, tok: int, hi: float, lo: float) -> None:
        em[a:b, tok] = np.log(hi)
        em[a:b, [c for c in range(3) if c != tok]] = np.log(lo)

    bump(20, 41, 1, 0.90, 0.05)     # region A: 'a' heard at 0.4-0.8 s
    bump(120, 141, 1, 0.85, 0.075)  # region B: weaker alternative for 'a' at 2.4-2.8 s
    bump(200, 221, 2, 0.95, 0.05)   # word 'b' at 4.0-4.4 s
    em[41:120, 1] = np.log(0.02)    # gap: 'a' is implausible between A and B
    em[141:200, 1] = np.log(0.02)
    em[221:, 1] = np.log(0.02)
    return em


def run_viterbi(em, prior_centers=None):
    lo = np.zeros(2, dtype=np.int64)
    hi = np.full(2, len(em), dtype=np.int64)
    return viterbi(em, [1, 2], lo, hi, prior_centers=prior_centers)


def span_center_s(span) -> float:
    return (span[0] + span[1]) / 2 * FRAME_S


def test_strong_prior_moves_span_toward_its_center(monkeypatch):
    monkeypatch.setenv("ULTRAAI_ASR_PRIOR", "10.0")  # strong beta, default sigma 0.3
    em = synth_em()
    base = run_viterbi(em)
    pri = run_viterbi(em, prior_centers=np.array([2.6, np.nan]))
    # without prior the word sits at its acoustic home in region A (0.4 s)
    assert 0.3 <= base[0][0] * FRAME_S <= 0.9
    # with the prior the span moved toward 2.6 s
    assert pri[0][0] * FRAME_S > base[0][0] * FRAME_S + 1.0
    assert abs(span_center_s(pri[0]) - 2.6) < abs(span_center_s(base[0]) - 2.6)
    # the other word is untouched
    assert pri[1] == base[1]


def test_zero_beta_or_no_priors_is_identical_to_no_prior(monkeypatch):
    em = synth_em()
    base = run_viterbi(em)
    centers = np.array([2.6, np.nan])
    monkeypatch.setenv("ULTRAAI_ASR_PRIOR", "0")
    assert run_viterbi(em, prior_centers=centers) == base          # beta = 0 disables
    monkeypatch.setenv("ULTRAAI_ASR_PRIOR", "1.0")
    assert run_viterbi(em, prior_centers=None) == base             # None = off
    assert run_viterbi(em, prior_centers=np.full(2, np.nan)) == base  # all-NaN centers = off
    monkeypatch.delenv("ULTRAAI_ASR_PRIOR")
    assert run_viterbi(em) == base                                  # default config unchanged


def test_prior_centers_length_must_match_targets():
    em = synth_em()
    lo = np.zeros(2, dtype=np.int64)
    hi = np.full(2, len(em), dtype=np.int64)
    with pytest.raises(ValueError, match="prior_centers"):
        viterbi(em, [1, 2], lo, hi, prior_centers=np.array([2.6]))


def asrw(start: float, text: str, prob: float = 0.9):
    return SimpleNamespace(start=start, end=start + 0.3, text=text, prob=prob)


def test_match_picks_time_near_similar_word_and_ignores_dissimilar():
    words = [asrw(1.00, "hello"), asrw(1.50, "hello"), asrw(1.10, "world")]
    # nearest similar word to ref=1.2 is the one at 1.0 (1.5 is similar but farther)
    assert match_asr_time("hello", words, ref=1.2) == pytest.approx(1.0)
    # a dissimilar word that is even nearer must be ignored
    words2 = [asrw(1.00, "hello"), asrw(1.25, "zebra")]
    assert match_asr_time("hello", words2, ref=1.2) == pytest.approx(1.0)


def test_match_respects_window_and_normalized_key():
    assert match_asr_time("hello", [asrw(1.9, "hello")], ref=1.0) == pytest.approx(1.9)  # within 2.0 s
    assert np.isnan(match_asr_time("hello", [asrw(3.1, "hello")], ref=1.0))              # outside 2.0 s
    assert np.isnan(match_asr_time("hello", [asrw(1.0, "zebra")], ref=1.0))              # dissimilar
    assert np.isnan(match_asr_time("", [asrw(1.0, "hello")], ref=1.0))                   # empty lyric word
    # normalized key: punctuation/case variants still match
    assert match_asr_time("Don't", [asrw(1.0, "dont")], ref=1.0) == pytest.approx(1.0)


def test_config_defaults_registered():
    assert config.get("asr_prior") == "2.0"
    assert config.get_float("asr_prior_sigma") == pytest.approx(0.3)
    assert config.env_of("asr_prior") == "ULTRAAI_ASR_PRIOR"
    assert config.env_of("asr_prior_sigma") == "ULTRAAI_ASR_PRIOR_SIGMA"
    assert {"asr_prior", "asr_prior_sigma"} <= {o.key for o in config.OPTS}


def test_config_env_override_read_at_call_time(monkeypatch):
    monkeypatch.setenv("ULTRAAI_ASR_PRIOR", "0")
    monkeypatch.setenv("ULTRAAI_ASR_PRIOR_SIGMA", "0.5")
    assert config.get_float("asr_prior") == 0.0
    assert config.get_float("asr_prior_sigma") == pytest.approx(0.5)
