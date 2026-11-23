from ultraai import issues as iss
from ultraai.generate import Syllable
from ultraai.transcribe import AsrWord


def _song():
    # two lines: "hello world" at 1 s, "good night" at 5 s
    syl = [Syllable("hel", 1.0, 1.2, word=0, line=0, src_line=0), Syllable("lo ", 1.2, 1.5, word=0, line=0, src_line=0),
           Syllable("world ", 1.6, 2.0, word=1, line=0, src_line=0),
           Syllable("good ", 5.0, 5.3, word=2, line=1, src_line=1), Syllable("night ", 5.4, 6.0, word=3, line=1, src_line=1)]
    for s in syl:
        s.score, s.voiced, s.stability = 0.5, 0.9, 0.2
    return syl


def test_features_whisper_agreement():
    asr = [AsrWord(1.05, 1.4, "hello", 0.9), AsrWord(1.6, 2.0, "world", 0.9),
           AsrWord(8.0, 8.3, "good", 0.9)]  # "good" heard 3 s later, "night" not at all
    ws = iss.word_features(_song(), {0: 1.0, 1: 1.6, 2: 5.0, 3: 5.4}, [1.0, 5.0], asr, None, True, False)
    assert [w.text for w in ws] == ["hello", "world", "good", "night"]
    assert ws[0].f["asr_dt"] < 0.1 and ws[0].f["asr_line_heard"] == 1.0
    assert abs(ws[2].f["asr_dt"] - 3.0) < 1e-6 and ws[3].f["asr_none"] == 1.0
    assert ws[2].f["asr_line_heard"] == 0.0
    assert set(iss.FEATURES) <= set(ws[0].f) | {"loud_rel"}


def test_leftover_and_issues():
    asr = [AsrWord(10.0 + i * 0.3, 10.2 + i * 0.3, w, 0.9) for i, w in enumerate("this is not in the lyrics".split())]
    left = iss.leftover_asr(asr, [(1.0, "hello"), (5.0, "good")])
    assert left and left[0][2].startswith("this is")
    ws = iss.word_features(_song(), {}, [None, None], asr, None, False, False)
    for w in ws:
        w.risk = 0.9 if w.line == 1 else 0.1
        w.reasons = iss.reasons(w.f)
    out = iss.line_issues(ws, 0.5, left)
    assert [i["kind"] for i in out].count("missing_lyrics") == 1
    timing = [i for i in out if i["kind"] == "timing"]
    assert len(timing) == 1 and timing[0]["line"] == 1 and timing[0]["text"] == "good night"
