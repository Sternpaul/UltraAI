from ultraai.structure import fill_times, match
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
