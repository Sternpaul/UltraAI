"""PR-10 (roadmap B3): double-check consensus auto-correct.

With more than one pitch check model configured, when >=2 check models agree with each
other on a sung note (octave-folded pitches within 1 semitone) and their consensus differs
from the main pitch model's pitch by >=2 semitones, the note pitch is corrected to the
consensus (median of the agreeing models, rounded to a semitone) and the sidecar gets an
'auto-fixed' issue instead of a plain 'models_pitch' disagreement.  Synthetic pitch
tracks only - no models, no audio.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

import ultraai.generate as gen
from ultraai.generate import Syllable, double_check_issues, to_song
from ultraai.issues import AUTOFIX_MIN_MODELS, consensus_autofix
from ultraai.pitch import PitchTrack
from ultraai.ultrastar import reader, writer
from ultraai.ultrastar.model import NOTE_KINDS


def track(pitch: float, dur: float = 4.0) -> PitchTrack:
    """Synthetic pitch track: one constant, fully voiced midi value over [0, dur)."""
    times = np.arange(0.0, dur, 0.01)
    return PitchTrack(times, np.full(len(times), pitch),
                      np.full(len(times), 0.9), np.full(len(times), -20.0))


def syl(start: float = 0.5, end: float = 1.0, pitch: int = 60, text: str = "la ",
        line: int = 0) -> Syllable:
    return Syllable(text, start, end, pitch=pitch, word=0, line=line, score=1.0, voiced=1.0)


def run_double_check(monkeypatch, main: float, checks: dict[str, float], autofix: str = "1",
                     syls: list[Syllable] | None = None):
    """Run generate.double_check_issues with synthetic pitch tracks (no models, no audio)."""
    monkeypatch.delenv("ULTRAAI_PITCH", raising=False)  # main model = registered default
    monkeypatch.setenv("ULTRAAI_PITCH_CHECK", ",".join(checks))
    monkeypatch.delenv("ULTRAAI_PITCH2", raising=False)
    monkeypatch.delenv("ULTRAAI_SEP_CHECK", raising=False)
    monkeypatch.delenv("ULTRAAI_SEP2", raising=False)
    monkeypatch.setenv("ULTRAAI_CHECK_AUTOFIX", autofix)
    tracks = {m: track(p) for m, p in checks.items()}
    monkeypatch.setattr(gen, "pitch_track", lambda vocals, wave, model: tracks[model])
    a = SimpleNamespace(vocals=Path("vocals.wav"), wave=np.zeros(16000), pitch=track(main))
    syls = syls if syls is not None else [syl()]
    issues = double_check_issues(Path("song.mp3"), "melband", syls, a)
    return syls, issues


# ---- the pure consensus rule --------------------------------------------------

def test_consensus_rule_two_agree_and_differ_from_main():
    got = consensus_autofix(60.0, {"a": 55.0, "b": 55.5, "c": 63.0})
    assert got is not None
    consensus, models = got
    assert models == ["a", "b"]                 # the 2 agreeing models, not the outlier
    assert abs(consensus - 55.25) < 1e-9        # median of the agreeing pitches


def test_consensus_rule_boundaries():
    assert consensus_autofix(60.0, {"a": 50.0, "b": 51.0}) is not None   # exactly 1 semitone apart = agree
    assert consensus_autofix(60.0, {"a": 50.0, "b": 51.1}) is None       # > 1 semitone apart
    assert consensus_autofix(60.0, {"a": 58.5, "b": 59.5}) is None        # consensus 59: < 2 semitones from main
    assert consensus_autofix(60.0, {"a": 55.0}) is None                  # fewer than 2 check models
    assert consensus_autofix(60.0, {"a": 60.0, "b": 60.5}) is None        # main already matches the majority
    assert AUTOFIX_MIN_MODELS == 2


# ---- (a) 3 check models, 2 agree vs main -> corrected to the median -----------

def test_two_of_three_agree_autofixes_to_median(monkeypatch):
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 55.0, "rmvpe": 55.5, "fcpe": 63.0})
    s = syls[0]
    assert s.auto_fixed is True
    assert s.pitch == 55                                    # round(median(55, 55.5)) = 55
    kinds = [i["kind"] for i in issues]
    assert kinds == ["auto-fixed"]                          # no plain disagreement issue
    iss = issues[0]
    assert len(iss["models"]) == 2 and "FCPE" not in " ".join(iss["models"])
    assert iss["old_pitch"] == 60 and iss["new_pitch"] == 55
    assert iss["line"] == s.line and iss["text"] == "la"


def test_autofix_shifts_melisma_segments_by_the_same_delta(monkeypatch):
    s = syl()
    s.segments = [(0.5, 0.7, 60), (0.7, 1.0, 62)]          # melisma on the main pitch
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 55.0, "rmvpe": 55.5},
                                    syls=[s])
    assert syls[0].pitch == 55
    assert syls[0].segments == [(0.5, 0.7, 55), (0.7, 1.0, 57)]  # shape kept, shifted -5
    assert [i["kind"] for i in issues] == ["auto-fixed"]


# ---- (b) only 1 agrees -> unchanged, normal disagreement issue ----------------

def test_single_agreement_keeps_disagreement_issue(monkeypatch):
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 56.0, "rmvpe": 63.0, "fcpe": 66.0})
    assert syls[0].pitch == 60 and syls[0].auto_fixed is False
    assert [i["kind"] for i in issues] == ["models_pitch"]
    assert issues[0]["risk"] > 0.6


# ---- (c) main matches the majority -> unchanged --------------------------------

def test_main_matches_majority_unchanged(monkeypatch):
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 60.0, "rmvpe": 60.5})
    assert syls[0].pitch == 60 and syls[0].auto_fixed is False
    assert issues == []


# ---- (d) config off -> unchanged ----------------------------------------------

def test_check_autofix_disabled_keeps_disagreement_issue(monkeypatch):
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 55.0, "rmvpe": 55.5, "fcpe": 63.0},
                                    autofix="0")
    assert syls[0].pitch == 60 and syls[0].auto_fixed is False
    assert [i["kind"] for i in issues] == ["models_pitch"]


# ---- (e) only 1 check model configured -> never auto-fixes --------------------

def test_single_check_model_never_autofixes(monkeypatch):
    syls, issues = run_double_check(monkeypatch, 60.0, {"pesto": 55.0})
    assert syls[0].pitch == 60 and syls[0].auto_fixed is False
    assert [i["kind"] for i in issues] == ["models_pitch"]


# ---- (f) .txt writer round-trip keeps the corrected pitch and the marker ------

def test_writer_roundtrip_keeps_corrected_pitch_and_autofix_marker(monkeypatch):
    syls, _ = run_double_check(monkeypatch, 60.0, {"pesto": 55.0, "rmvpe": 55.5, "fcpe": 63.0})
    song = to_song(syls, {"TITLE": "t", "ARTIST": "a"}, "song.mp3")
    note = next(song.notes())
    assert note.pitch == 55 - 60                       # corrected pitch in the chart
    assert note.auto_fixed is True
    assert note.kind in NOTE_KINDS                     # kind stays a valid USDX kind

    text = writer.dumps(song)
    assert "#ULTRAAI-AUTOFIX:1:0" in text              # marker in the post-'E' trailer
    back = reader.parse(text)
    bnote = next(back.notes())
    assert bnote.pitch == note.pitch == -5             # corrected pitch survives the round-trip
    assert bnote.auto_fixed is True                    # marker survives the round-trip
    assert bnote.kind == note.kind and bnote.kind in NOTE_KINDS
    assert back.warnings == []

    plain = writer.dumps(to_song([syl()], {"TITLE": "t"}, "song.mp3"))  # no autofix -> no trailer
    assert "#ULTRAAI-AUTOFIX" not in plain
    assert next(reader.parse(plain).notes()).auto_fixed is False
