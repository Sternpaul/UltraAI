"""Numerals in lyrics -> spoken words (lyrics.expand_numbers, ULTRAAI_EXPAND_NUMBERS).

Whisper hears the sung words ("twenty two"), so digits in the lyrics would land on one
note with empty sung syllables.  expand_numbers rewrites them for processing and the
output .txt; eval metrics expand both sides so "22" == "twenty two".  No network, no songs/.
"""

from ultraai import config
from ultraai.batch import Track, detect_language, expand_candidates, find_lyrics
from ultraai.eval.metrics import compare
from ultraai.lrc import LrcLine, LrcResult
from ultraai.lyrics import expand_numbers
from ultraai.ultrastar.reader import parse

# ---- unit: expand_numbers -----------------------------------------------------


def test_expand_english_cardinals():
    assert expand_numbers("at the age of 22", "en") == "at the age of twenty-two"
    assert expand_numbers("0", "en") == "zero"
    assert expand_numbers("1,000", "en") == "one thousand"
    assert expand_numbers("9999 bottles", "en") == "nine thousand, nine hundred and ninety-nine bottles"


def test_expand_year_reading():
    assert expand_numbers("in 1999", "en") == "in nineteen ninety-nine"
    assert expand_numbers("since 2000", "en") == "since two thousand"  # 2000 also has a year reading
    assert expand_numbers("12345", "en") == "12345"  # out of range: unchanged


def test_expand_ordinals_and_decimals():
    assert expand_numbers("1st", "en") == "first"
    assert expand_numbers("2nd 3rd 21st", "en") == "second third twenty-first"
    assert expand_numbers("3.5", "en") == "three point five"


def test_expand_german():
    assert expand_numbers("ich bin 22", "de") == "ich bin zweiundzwanzig"


def test_unsupported_lang_unchanged():
    assert expand_numbers("at 22", "el") == "at 22"   # num2words has no Greek converter
    assert expand_numbers("at 22", "xx") == "at 22"


def test_non_numbers_unchanged():
    for tok in ("$5", "COVID-19", "3D", "hello", "don't", "12,34", "v1.2"):
        assert expand_numbers(tok, "en") == tok


def test_line_structure_preserved():
    text = "I was 22\n  in 1999!\n(1st time)\n"
    out = expand_numbers(text, "en")
    assert out.splitlines() == ["I was twenty-two", "  in nineteen ninety-nine!", "(first time)"]
    assert "\n" in out and out.endswith("\n")


def test_language_name_accepted():
    assert expand_numbers("22", "English") == "twenty-two"


# ---- config -------------------------------------------------------------------


def test_config_default_on(monkeypatch):
    assert config.get("expand_numbers") == "1"
    assert config.get_bool("expand_numbers") is True
    assert config.env_of("expand_numbers") == "ULTRAAI_EXPAND_NUMBERS"
    monkeypatch.setenv("ULTRAAI_EXPAND_NUMBERS", "0")
    assert config.get_bool("expand_numbers") is False


# ---- wiring: candidates (batch + worker choke point) ---------------------------


def _sidecar_track(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    (tmp_path / "song.lrc").write_text(
        "[00:01.00]I was 22\n[00:05.50]back in 1999\n[00:09.00]the 1st time\n", encoding="utf-8")
    return Track(audio, "Artist", "Title")


def test_find_lyrics_convert_path_expands_and_keeps_times(tmp_path):
    track = _sidecar_track(tmp_path)
    cands, source, line_times = find_lyrics(track)          # sidecar: no network
    assert source == "sidecar.lrc" and line_times is None
    code, _ = detect_language(cands[0].texts)
    assert code == "en"
    cands = expand_candidates(cands, code)                   # what batch.convert does next
    c = cands[0]
    # synced line texts expanded, times untouched (a .lrc sidecar keeps its stamps in plain)
    assert c.texts == ["I was twenty-two", "back in nineteen ninety-nine", "the first time"]
    assert [ln.time for ln in c.lines] == [1.0, 5.5, 9.0]
    assert c.plain == ["[00:01.00]I was twenty-two", "[00:05.50]back in nineteen ninety-nine",
                       "[00:09.00]the first time"]


def test_expand_candidates_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRAAI_EXPAND_NUMBERS", "0")
    track = _sidecar_track(tmp_path)
    cands, _, _ = find_lyrics(track)
    code, _ = detect_language(cands[0].texts)
    out = expand_candidates(cands, code)
    assert out[0].texts == ["I was 22", "back in 1999", "the 1st time"]
    assert [ln.time for ln in out[0].lines] == [1.0, 5.5, 9.0]


def test_expand_candidates_plain_only():
    c = LrcResult("a", "t", 0.0, [], ["22 and 3.5"])
    out = expand_candidates([c], "en")
    assert out[0].plain == ["twenty-two and three point five"]
    assert out[0].lines == []
    # synced line text expanded, time kept
    c2 = LrcResult("a", "t", 0.0, [LrcLine(2.5, "22")], ["22"])
    out2 = expand_candidates([c2], "en")
    assert out2[0].lines[0].text == "twenty-two" and out2[0].lines[0].time == 2.5


# ---- metrics: "22" == "twenty two" ---------------------------------------------

_SONG = ("#TITLE:T\n#ARTIST:A\n#LANGUAGE:English\n#MP3:a.mp3\n#BPM:300\n#GAP:0\n"
         ": 0 4 0 {w}\n- 8\nE\n")


def test_compare_treats_digits_as_words():
    ref = parse(_SONG.format(w="22"))
    hyp = parse(_SONG.format(w="twenty-two"))
    m = compare(ref, hyp)
    assert m["word_recall"] == 1.0 and m["word_precision"] == 1.0
    assert m["onset_median_ms"] == 0


def test_compare_digits_mismatch_without_expansion_would_fail():
    # sanity: the tokens really differ before expansion (norm_token("22") != norm_token("twenty-two"))
    from ultraai.eval.metrics import norm_token
    assert norm_token("22") != norm_token("twenty-two")
    ref = parse(_SONG.format(w="22"))
    hyp = parse(_SONG.format(w="99"))
    assert compare(ref, hyp)["word_recall"] == 0.0
