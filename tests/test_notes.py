from ultraai.generate import Syllable, harmonize_repeats, mark_note_kinds


def _line(li, t0, pitches, words=("whoa ", "on ", "the ", "edge ")):
    out = []
    for k, (w, p) in enumerate(zip(words, pitches)):
        s = Syllable(w, t0 + k * 0.5, t0 + k * 0.5 + 0.4, pitch=p, word=li * 10 + k, line=li)
        s.voiced, s.stability = 0.9, 0.2
        out.append(s)
    return out


def test_repeats_get_the_same_notes():
    a = _line(0, 0, [67, 64, 64, 62])
    b = _line(1, 5, [67, 63, 64, 50])      # one tracker octave error, one slightly off
    c = _line(2, 10, [67, 64, 65, 62])
    d = _line(3, 15, [72, 69, 69, 67])     # key change: sung differently, left alone
    syl = a + b + c + d
    props = []
    assert harmonize_repeats(syl, props) == 3
    assert len(props) == 1 and props[0][0] == d          # proposed for review instead
    assert [x.pitch for x in a] == [x.pitch for x in b] == [x.pitch for x in c] == [67, 64, 64, 62]
    assert [x.pitch for x in d] == [72, 69, 69, 67]


def test_golden_on_big_jumps_and_same_in_repeats():
    import ultraai.generate as g

    lines = [_line(i, 5 * i, [60, 60, 67, 60], ("la ", "la ", "whoa ", "la ")) for i in range(3)]
    syl = [s for ln in lines for s in ln] + _line(9, 20, [60, 61, 60, 61], ("x ", "y ", "z ", "w "))
    old = g.GOLDEN_SHARE
    g.GOLDEN_SHARE = 0.07  # 1 of 16 picked by score, the repeats follow
    try:
        mark_note_kinds(syl)
    finally:
        g.GOLDEN_SHARE = old
    assert all(ln[2].kind == "*" for ln in lines)           # the jump up to "whoa", in every repeat
    assert sum(s.kind == "*" for s in syl) == 3
