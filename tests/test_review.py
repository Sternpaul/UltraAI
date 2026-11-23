from ultraai.review import apply_edits
from ultraai.ultrastar.reader import read

TXT = "#VERSION:1.0.0\n#TITLE:T\n#ARTIST:A\n#MP3:a.mp3\n#BPM:300\n#GAP:1000\n: 0 4 0 a\n- 10\n: 20 4 0 b\n- 30\n: 40 4 0 c\nE\n"


def test_apply_edits_shifts_line_and_gap(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text(TXT)
    issues = apply_edits(p, {"1": 100.0}, -20.0)  # 100 ms = 2 beats at BPM 300
    s = read(p)
    lines = s.lines()
    assert lines[1].notes[0].start == 22
    assert lines[0].end <= lines[0].break_beat <= lines[1].start
    assert s.gap_ms == 980
    assert (tmp_path / "s.txt.bak").read_text() == TXT  # original kept, not a .txt (USDX ignores it)
    assert issues == []
