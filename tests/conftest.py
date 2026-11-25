"""Shared synthetic fixtures: tiny 16 kHz waveforms, pitch tracks and alignments.

Everything here is pure numpy — no model downloads, no network, no songs/ folder.
"""

import os

import numpy as np
import pytest

from ultraai.align import AlignedWord, CharTime
from ultraai.lyrics import Lyrics
from ultraai.pitch import PitchTrack

SR = 16000


@pytest.fixture(autouse=True)
def _clean_ultraai_env(monkeypatch):
    """Options are read from ULTRAAI_* env vars at call time; start every test at the defaults."""
    for key in [k for k in os.environ if k.startswith("ULTRAAI_")]:
        monkeypatch.delenv(key, raising=False)


def synth_wave(bursts, duration, sr=SR):
    """Mono float32 wave: sine bursts ``(start, end, freq_hz)`` on silence, so the
    sounding spans are known exactly."""
    n = int(round(duration * sr))
    t = np.arange(n) / sr
    wave = np.zeros(n, dtype=np.float32)
    for start, end, freq in bursts:
        m = (t >= start) & (t < end)
        wave[m] += 0.5 * np.sin(2.0 * np.pi * freq * t[m])
    return wave


def synth_track(regions, duration, frame=0.01, conf=0.9, loud_db=-20.0):
    """PitchTrack with voiced regions ``(start, end, midi)``; unvoiced (nan, conf 0) elsewhere."""
    n = int(round(duration / frame))
    times = (np.arange(n) + 0.5) * frame          # frame centres
    midi = np.full(n, np.nan)
    confidence = np.zeros(n)
    loudness = np.full(n, -120.0)
    for start, end, m in regions:
        i0 = int(np.searchsorted(times, start))
        i1 = int(np.searchsorted(times, end))
        midi[i0:i1] = m
        confidence[i0:i1] = conf
        loudness[i0:i1] = loud_db
    return PitchTrack(times, midi, confidence, loudness)


def aligned_words(lyrics: Lyrics, starts, score=0.9, char_s=0.05):
    """One AlignedWord per lyric word, word i starting at ``starts[i]`` (mirrors align.py)."""
    out = []
    for wi, w in enumerate(lyrics.words):
        t = starts[wi]
        chars = [CharTime(ch, t + k * char_s, t + (k + 1) * char_s, score)
                 for k, ch in enumerate(w.align)]
        syl_starts, k = [], 0
        for syl in w.syllable_align:
            syl_starts.append(chars[min(k, len(chars) - 1)].start)
            k += len(syl)
        out.append(AlignedWord(wi, chars, syl_starts, chars[0].start, chars[-1].end, score))
    return out


@pytest.fixture
def wave_factory():
    return synth_wave


@pytest.fixture
def track_factory():
    return synth_track


@pytest.fixture
def make_aligned():
    return aligned_words
