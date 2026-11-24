"""tempo.py: the beats cache must be usable when the decoded mix.wav was deleted."""

import numpy as np

from ultraai.tempo import detect_beats


def test_detect_beats_serves_cache_when_mix_wav_is_gone(tmp_path):
    # scripts/process_library.py deletes mix.wav after each song to save disk, keeping
    # mix.beats.npz; generate.py's tempo block must then not re-run ffmpeg (to_wav).
    # librosa is not installed in the test env, so any decode attempt would raise.
    mix = tmp_path / "mix.wav"
    np.savez(mix.with_suffix(".beats.npz"), tempo=123.0, beats=np.array([0.0, 0.5, 1.0]))
    assert not mix.exists()
    # the re-decode guard as written in generate.py (generate_from_lines):
    assert not (not mix.exists() and not mix.with_suffix(".beats.npz").exists())
    tempo, beats = detect_beats(mix)
    assert tempo == 123.0
    assert list(beats) == [0.0, 0.5, 1.0]
