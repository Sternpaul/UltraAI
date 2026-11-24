"""choose_grid (ultraai/tempo.py) with synthetic beat arrays."""

import numpy as np
import pytest

from ultraai.tempo import FALLBACK_BPM, choose_grid


def test_steady_beats_lock_the_tempo():
    beats = np.arange(0.0, 8.0, 0.5)                      # 16 beats at 120 quarter-notes/min
    grid = choose_grid(120.0, beats, first_note=3.2)
    assert grid.tempo == pytest.approx(120.0, abs=1e-6)
    assert grid.bpm == 240.0                              # 62.5 ms beats, inside the 37..75 ms target
    assert 0.037 <= grid.beat_s <= 0.075
    assert grid.gap_s == pytest.approx(3.0)               # last beat boundary at or before the first note
    assert grid.gap_s <= 3.2
    assert 3.2 - grid.gap_s <= 0.5 + 1e-9                 # ... within one quarter note of it


def test_jittery_beats_fall_back_to_fixed_grid():
    beats = np.cumsum(np.tile([0.3, 0.9], 10))            # alternating: nothing stays on tempo
    grid = choose_grid(100.0, beats, first_note=1.0)
    assert grid.bpm == FALLBACK_BPM and grid.tempo is None
    assert grid.gap_s == 1.0                              # fixed 50 ms grid starting at the first note


def test_too_few_or_no_beats_fall_back():
    grid = choose_grid(120.0, np.array([]), 2.0)
    assert grid.bpm == FALLBACK_BPM and grid.gap_s == 2.0 and grid.tempo is None
    grid = choose_grid(120.0, np.arange(0.0, 4.0, 0.5), 2.0)   # only 8 beats (< 16)
    assert grid.bpm == FALLBACK_BPM and grid.gap_s == 2.0 and grid.tempo is None
