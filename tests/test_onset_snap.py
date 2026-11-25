"""onsets.py: spectral-flux onset detection on a synthetic wave, onset snapping, cache."""

import numpy as np
import pytest

from ultraai.align import AlignedWord
from ultraai.onsets import detect_onsets, onset_envelope, onsets_for, pick_peaks, snap_words_to_onsets

SR = 16000


def synth_bursts(bursts: list[float], dur: float = 3.0, freq: float = 440.0) -> np.ndarray:
    """Silent 16 kHz wave with short tone bursts starting at the given times."""
    wave = np.zeros(int(dur * SR))
    n = int(0.15 * SR)
    t = np.arange(n) / SR
    ramp = np.clip(np.minimum(t / 0.005, (0.15 - t) / 0.010), 0.0, 1.0)  # 5 ms in, 10 ms out
    for b in bursts:
        i0 = int(round(b * SR))
        wave[i0:i0 + n] += 0.5 * np.sin(2 * np.pi * freq * t) * ramp
    return wave


def word(start: float, end: float, syls: list[float] | None = None, index: int = 0) -> AlignedWord:
    return AlignedWord(index=index, chars=[], syllable_starts=list(syls or [start]),
                       start=start, end=end, score=1.0)


def test_envelope_and_peak_pick_find_tone_bursts():
    bursts = [0.5, 1.2, 2.0]
    wave = synth_bursts(bursts)
    env = onset_envelope(wave)
    assert env.ndim == 1 and env.size > 0 and (env >= 0).all()
    onsets = pick_peaks(env)
    assert len(onsets) >= len(bursts)
    for b in bursts:  # every true burst start has a detected onset within ±30 ms
        assert np.min(np.abs(onsets - b)) <= 0.030
    for o in onsets:  # no onsets far outside the bursts (attack and release transients are ok)
        assert any(abs(o - b) <= 0.030 or b - 0.030 <= o <= b + 0.18 for b in bursts)


def test_detect_onsets_on_silent_wave_is_empty():
    assert detect_onsets(np.zeros(SR)).size == 0


def test_snap_moves_word_near_onset():
    w = word(1.00, 1.30, syls=[1.00, 1.15])
    words, stats = snap_words_to_onsets([w], np.array([1.07]), 0.08)
    assert w.start == pytest.approx(1.07)
    assert w.end == pytest.approx(1.37)
    assert w.syllable_starts == pytest.approx([1.07, 1.22])
    assert stats["count"] == 1
    assert stats["max_abs_delta"] == pytest.approx(0.07)


def test_snap_leaves_word_far_from_any_onset():
    w = word(1.00, 1.30, syls=[1.00, 1.15])
    _, stats = snap_words_to_onsets([w], np.array([2.00]), 0.08)
    assert w.start == 1.00 and w.end == 1.30 and w.syllable_starts == [1.00, 1.15]
    assert stats == {"count": 0, "max_abs_delta": 0.0}


def test_snap_clamps_against_previous_words_end():
    w0 = word(1.00, 1.20, index=0)
    w1 = word(1.25, 1.50, index=1)
    # onset 1.175 is within win of w1 but would start it before w0.end - 0.02 = 1.18
    _, stats = snap_words_to_onsets([w0, w1], np.array([1.175]), 0.08)
    assert w1.start == pytest.approx(1.18)
    assert w1.start >= w0.end - 0.02
    assert stats["count"] == 1


def test_snap_never_crosses_next_word():
    w0 = word(1.30, 1.45, index=0)
    w1 = word(1.40, 1.60, index=1)
    # onset 1.375 is within win of w0 but later than w1.start - 0.05 = 1.35
    snap_words_to_onsets([w0, w1], np.array([1.375]), 0.08)
    assert w0.start == pytest.approx(1.35)
    assert w0.start <= w1.start - 0.05
    assert w0.end == pytest.approx(1.50)


def test_snap_keeps_syllable_starts_non_decreasing_and_stats():
    ws = [word(0.50, 0.70, syls=[0.50, 0.60], index=0),
          word(0.80, 1.00, syls=[0.80], index=1),
          word(1.10, 1.40, syls=[1.10, 1.20, 1.30], index=2)]
    onsets = np.array([0.56, 0.83, 2.00])  # 2.00 is too far from word 3
    words, stats = snap_words_to_onsets(ws, onsets, 0.08)
    assert stats["count"] == 2
    assert stats["max_abs_delta"] == pytest.approx(0.06)
    for w in words:
        assert all(w.syllable_starts[k] >= w.syllable_starts[k - 1] for k in range(1, len(w.syllable_starts)))
    assert [w.start for w in words] == pytest.approx([0.56, 0.83, 1.10])
    assert [w.start for w in words] == sorted(w.start for w in words)  # order preserved


def test_onsets_for_caches_next_to_vocals(tmp_path):
    vocals = tmp_path / "vocals.melband.wav"
    wave = synth_bursts([0.5])
    first = onsets_for(vocals, wave)
    cache = vocals.with_suffix(".onsets.npz")
    assert cache.exists()
    assert np.min(np.abs(first - 0.5)) <= 0.030
    second = onsets_for(vocals, np.zeros(SR))  # different wave: must serve the cache
    assert np.array_equal(second, first)
