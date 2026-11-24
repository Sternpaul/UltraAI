"""Song fit (B5b): key detection + snap, octave range, #VIDEOGAP cross-correlation.

Pure unit tests on synthetic pitch tracks / envelopes — no ffmpeg, no models, no network.
(no conftest on this branch: local helpers only)
"""

import numpy as np
import pytest

from ultraai import config
from ultraai.generate import Syllable, to_song
from ultraai.onsets import onset_envelope
from ultraai.pitch import PitchTrack
from ultraai.pitchfit import detect_key, fit_syllables, fold_octave, parse_octave_range, snap_to_key
from ultraai.tempo import Grid
from ultraai.ultrastar.writer import dumps
from ultraai.video import apply_videogap, best_lag, find_video

SR = 16000


def synth_track(pitches: list[float], frame: float = 0.01, conf: float = 0.9, reps: int = 20) -> PitchTrack:
    """PitchTrack that holds each pitch for ``reps`` frames (a synthetic melody)."""
    midi = np.concatenate([np.full(reps, p, dtype=float) for p in pitches])
    n = len(midi)
    return PitchTrack(np.arange(n) * frame, midi, np.full(n, conf), np.full(n, -20.0))


def syl(text: str, start: float, end: float, pitch: int, line: int = 0, word: int = 0,
        segments=None) -> Syllable:
    return Syllable(text, start, end, pitch=pitch, word=word, line=line, score=1.0,
                    voiced=1.0, segments=segments)


def song_of(syls) -> "object":
    return to_song(syls, {"TITLE": "T", "ARTIST": "A"}, "a.mp3", grid=Grid(300.0, 1.0, None))


# ---- key detection (Krumhansl-Schmuckler profiles) -----------------------------

def test_detect_key_c_major():
    p = synth_track([60, 62, 64, 65, 67, 69, 71, 72, 67, 64, 62, 60])
    assert detect_key(p) == (0, False)


def test_detect_key_a_minor():
    p = synth_track([57, 60, 64, 67, 69, 64, 60, 57, 62, 59, 57])
    assert detect_key(p) == (9, True)


def test_detect_key_weights_confidence_and_duration():
    # alternating C/E frames; which pitch class dominates depends on the confidence
    # weight (each frame contributes frame-seconds x confidence)
    n = 400
    midi = np.full(n, 60.0)
    midi[::2] = 64.0  # every other frame is E instead of C
    conf = np.full(n, 0.6)
    conf[::2] = 0.9
    p = PitchTrack(np.arange(n) * 0.01, midi, conf, np.full(n, -20.0))
    assert detect_key(p) == (4, True)          # E-heavy -> E minor
    conf = np.full(n, 0.9)
    conf[::2] = 0.6
    p = PitchTrack(np.arange(n) * 0.01, midi, conf, np.full(n, -20.0))
    assert detect_key(p) == (0, False)         # C-heavy -> C major


def test_detect_key_unvoiced_track_is_default():
    p = PitchTrack(np.arange(10) * 0.01, np.full(10, np.nan), np.zeros(10), np.full(10, -120.0))
    assert detect_key(p) == (0, False)


# ---- key snap -------------------------------------------------------------------

def test_snap_to_key_moves_to_nearest_scale_tone():
    out = snap_to_key(np.array([61, 66, 73]), 0, False)  # C# -> C, F# -> F (tie rounds down), C#5 -> C5
    assert list(out) == [60, 65, 72]


def test_snap_to_key_leaves_in_scale_pitches_unchanged():
    in_scale = np.array([60, 62, 64, 65, 67, 69, 71, 72, 48, 84])
    assert list(snap_to_key(in_scale, 0, False)) == list(in_scale)


def test_snap_to_key_minor_scale_and_scalar():
    assert snap_to_key(58, 9, True) == 57          # B -> A in A minor
    out = snap_to_key(np.array([58, 68]), 9, True)
    assert list(out) == [57, 67]


def test_snap_never_moves_more_than_6_semitones():
    rng = np.random.default_rng(1)
    ps = rng.integers(30, 100, size=500)
    for tonic in range(12):
        for minor in (False, True):
            out = snap_to_key(ps, tonic, minor)
            assert np.abs(out - ps).max() <= 6
            scale = (0, 2, 3, 5, 7, 8, 10) if minor else (0, 2, 4, 5, 7, 9, 11)
            assert all((int(o) - tonic) % 12 in scale for o in out)


# ---- octave range ----------------------------------------------------------------

def test_fold_octave_into_range():
    out = fold_octave(np.array([36, 84, 85, 60, 47]), 48, 72)
    assert list(out) == [48, 72, 61, 60, 59]  # in-range 60 stays; others by minimal octaves


def test_fold_octave_preserves_pitch_class_and_scalar():
    assert fold_octave(84, 48, 72) == 72
    ps = np.array([30, 50, 90, 103])
    out = fold_octave(ps, 48, 72)
    assert all((int(o) - int(p)) % 12 == 0 for o, p in zip(out, ps))
    assert ((out >= 48) & (out <= 72)).all()


def test_parse_octave_range():
    assert parse_octave_range("") is None
    assert parse_octave_range("48,72") == (48, 72)
    assert parse_octave_range("55:79") == (55, 79)
    assert parse_octave_range("72,48") == (48, 72)
    with pytest.raises(ValueError):
        parse_octave_range("48")


# ---- #VIDEOGAP cross-correlation --------------------------------------------------

def burst_wave(times: list[float], dur: float = 4.0) -> np.ndarray:
    wave = np.zeros(int(dur * SR))
    n = int(0.15 * SR)
    t = np.arange(n) / SR
    ramp = np.clip(np.minimum(t / 0.005, (0.15 - t) / 0.010), 0.0, 1.0)
    for b in times:
        i0 = int(round(b * SR))
        wave[i0:i0 + n] += 0.5 * np.sin(2 * np.pi * 440.0 * t) * ramp
    return wave


def test_best_lag_recovers_delayed_envelope_within_one_frame():
    rng = np.random.default_rng(0)
    env_a = np.zeros(3000)
    env_a[rng.choice(np.arange(50, 2950), size=120, replace=False)] = rng.uniform(0.5, 1.0, 120)
    k = 137  # frames = 1.37 s at 16 kHz / 160 hop
    env_b = np.zeros_like(env_a)
    env_b[k:] = env_a[:-k]  # env_b is env_a delayed: the video starts LATER
    lag = best_lag(env_a, env_b, sr=SR, hop=160)
    assert lag == pytest.approx(k * 160 / SR, abs=160 / SR)  # within one frame
    assert lag > 0  # positive = env_b (video) starts later than env_a (audio)


def test_best_lag_negative_when_video_starts_earlier():
    rng = np.random.default_rng(2)
    env_a = np.zeros(3000)
    env_a[rng.choice(np.arange(50, 2950), size=120, replace=False)] = rng.uniform(0.5, 1.0, 120)
    k = 200
    env_b = np.zeros_like(env_a)
    env_b[: len(env_a) - k] = env_a[k:]  # env_b advanced: video starts earlier
    assert best_lag(env_a, env_b, sr=SR, hop=160) == pytest.approx(-k * 160 / SR, abs=160 / SR)


def test_best_lag_on_real_onset_envelopes():
    w1 = burst_wave([0.5, 1.1, 1.7, 2.3, 2.9, 3.4])
    delay = 1.37
    w2 = np.concatenate([np.zeros(int(delay * SR)), w1])[: len(w1)]  # same audio, video delayed
    lag = best_lag(onset_envelope(w1), onset_envelope(w2), SR)
    assert lag == pytest.approx(delay, abs=0.011)  # within one 10 ms frame


def test_find_video_sibling_same_stem(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    assert find_video(audio) is None
    (tmp_path / "song.lrc").write_text("[00:01.00]hi")
    assert find_video(audio) is None  # not a video extension
    video = tmp_path / "song.mp4"
    video.write_bytes(b"")
    assert find_video(audio) == video
    (tmp_path / "other.mkv").write_bytes(b"")
    assert find_video(audio) == video  # same stem only


def test_no_videogap_header_without_video(monkeypatch, tmp_path):
    monkeypatch.delenv("ULTRAAI_VIDEOGAP", raising=False)  # default is 1
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    song = song_of([syl("la ", 0.5, 0.9, 60)])
    apply_videogap(song, audio, {})
    assert song.get("VIDEOGAP") is None
    assert "#VIDEOGAP" not in dumps(song)


def test_no_videogap_header_when_config_off(monkeypatch, tmp_path):
    monkeypatch.setenv("ULTRAAI_VIDEOGAP", "0")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    (tmp_path / "song.mp4").write_bytes(b"")  # video exists, but the option is off
    song = song_of([syl("la ", 0.5, 0.9, 60)])
    apply_videogap(song, audio, {})
    assert song.get("VIDEOGAP") is None


def test_videogap_header_format_and_sign(monkeypatch, tmp_path):
    import ultraai.video as video

    monkeypatch.setenv("ULTRAAI_VIDEOGAP", "1")
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    (tmp_path / "song.mp4").write_bytes(b"")
    monkeypatch.setattr(video, "video_gap", lambda a: 2.5)  # positive = video starts later
    song = song_of([syl("la ", 0.5, 0.9, 60)])
    info = {}
    apply_videogap(song, audio, info)
    assert song.get("VIDEOGAP") == "2.50"  # seconds, 2 decimals
    assert "#VIDEOGAP:2.50" in dumps(song)
    assert info["videogap"] == 2.5


# ---- pipeline wiring: defaults are a no-op, features change only pitches ----------

def test_defaults_leave_generated_song_byte_identical(monkeypatch, tmp_path):
    monkeypatch.delenv("ULTRAAI_KEYSNAP", raising=False)
    monkeypatch.delenv("ULTRAAI_OCTAVE_RANGE", raising=False)
    monkeypatch.delenv("ULTRAAI_VIDEOGAP", raising=False)
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")  # no sibling video -> videogap is a no-op
    p = synth_track([60, 62, 64, 65, 67])
    seg = [(1.0, 1.2, 61), (1.2, 1.4, 66)]
    base = [syl("la ", 0.5, 0.9, 61, word=0), syl("lo ", 1.0, 1.4, 66, word=1, segments=seg)]
    song_default = song_of([syl("la ", 0.5, 0.9, 61, word=0), syl("lo ", 1.0, 1.4, 66, word=1, segments=[(1.0, 1.2, 61), (1.2, 1.4, 66)])])
    apply_videogap(song_default, audio, {})

    # same song with every feature explicitly OFF
    monkeypatch.setenv("ULTRAAI_KEYSNAP", "0")
    monkeypatch.setenv("ULTRAAI_OCTAVE_RANGE", "")
    monkeypatch.setenv("ULTRAAI_VIDEOGAP", "0")
    syls = [syl("la ", 0.5, 0.9, 61, word=0), syl("lo ", 1.0, 1.4, 66, word=1, segments=[(1.0, 1.2, 61), (1.2, 1.4, 66)])]
    assert fit_syllables(syls, p) == 0  # nothing touched
    song_off = song_of(syls)
    apply_videogap(song_off, audio, {})
    assert dumps(song_default) == dumps(song_off)
    assert base  # silence linters about the unused helper list


def test_keysnap_shifts_notes_and_melisma_but_not_timing(monkeypatch):
    monkeypatch.setenv("ULTRAAI_KEYSNAP", "1")
    monkeypatch.delenv("ULTRAAI_OCTAVE_RANGE", raising=False)
    p = synth_track([60, 62, 64, 65, 67, 69, 71, 72])  # C major
    syls = [syl("la ", 0.5, 0.9, 61, word=0),  # C# -> snapped to C
            syl("lo ", 1.0, 1.4, 66, word=1, segments=[(1.0, 1.2, 61), (1.2, 1.4, 66)])]
    changed = fit_syllables(syls, p)
    assert changed == 2
    assert syls[0].pitch == 60
    assert syls[1].pitch == 65               # the syllable's own pitch F# -> F
    assert [m for _, _, m in syls[1].segments] == [60, 65]
    song = song_of(syls)
    pitches = [n.pitch for n in song.notes()]  # stored relative to C4 (midi - 60)
    assert pitches == [0, 0, 5]  # 60, 60, 65
    starts = [(n.start, n.length) for n in song.notes()]
    song_ref = song_of([syl("la ", 0.5, 0.9, 60, word=0),
                        syl("lo ", 1.0, 1.4, 60, word=1, segments=[(1.0, 1.2, 60), (1.2, 1.4, 65)])])
    assert starts == [(n.start, n.length) for n in song_ref.notes()]  # timing untouched


def test_octave_range_folds_notes_in_song(monkeypatch):
    monkeypatch.delenv("ULTRAAI_KEYSNAP", raising=False)
    monkeypatch.setenv("ULTRAAI_OCTAVE_RANGE", "48,72")
    p = synth_track([60, 62, 64])
    syls = [syl("la ", 0.5, 0.9, 84, word=0), syl("lo ", 1.0, 1.4, 36, word=1)]
    assert fit_syllables(syls, p) == 2
    assert syls[0].pitch == 72 and syls[1].pitch == 48
    song = song_of(syls)
    assert [n.pitch for n in song.notes()] == [12, -12]  # midi - 60


def test_registered_options_exist_with_right_defaults():
    assert config.get("keysnap") == "0"
    assert config.get("octave_range") == ""
    assert config.get("videogap") == "1"
    assert config.env_of("keysnap") == "ULTRAAI_KEYSNAP"
    assert config.env_of("octave_range") == "ULTRAAI_OCTAVE_RANGE"
    assert config.env_of("videogap") == "ULTRAAI_VIDEOGAP"
