from ultraai.lyrics import clean_lines, language_code, prepare, romanize, syllabify


def test_romanize():
    assert romanize("Grönemeyer’s") == "gronemeyer's"
    assert romanize("Θέλω") == "thelo"
    assert romanize("Straße!") == "strasse"


def test_greeklish_is_the_one_canonical_function():
    from ultraai import batch
    from ultraai.lyrics import greeklish

    assert batch.greeklish is greeklish  # batch imports lyrics' function, no second copy
    sample = "Πού πας ρε Χαράλαμπε"
    assert greeklish(sample) == "Pou pas re Charalabe"  # μπ -> b (digraph), χ -> Ch
    assert batch.greeklish(sample) == greeklish(sample)


def test_clean_lines():
    assert clean_lines("[Chorus]\nHey (x2)\n\n[Verse: A] Yo") == ["Hey", "Hey", "Yo"]


def test_syllabify_keeps_text():
    for word, lang in [("beautiful,", "en"), ("Schwimmflügel", "de"), ("agkalia", "el"),
                       ("αγκαλιά", "el"), ("singing", "en"), ("Don't", "en")]:
        parts = syllabify(word, lang)
        assert "".join(parts) == word
    assert syllabify("again", "en") == ["a", "gain"]
    assert len(syllabify("singing", "en")) == 2
    assert syllabify("love", "en") == ["love"]  # silent e


def test_prepare_glues_punctuation():
    lyr = prepare("Yes - no\nNa ’nai", "el")
    assert [w.display for w in lyr.words] == ["Yes -", "no", "Na", "’nai"]
    assert all("".join(w.syllables) == w.display for w in lyr.words)
    assert lyr.words[2].line == 1


def test_language_code():
    assert language_code("Greek (romanized)") == "el"
    assert language_code("German") == "de"
    assert language_code(None) == "en"


def test_parentheses():
    out = clean_lines(["Take on me (take on me)", "(The world is a playground)", "I love you (ooh yeah)", "[Chorus]"])
    assert out == ["Take on me", "The world is a playground", "I love you ooh yeah"]


def test_backing_line_tail_overlap():
    out = clean_lines(["a taste for the world", "(The world is like a playground)"])
    assert out == ["a taste for the world", "is like a playground"]


def test_no_vowelless_syllables():
    assert syllabify("you", "en") == ["you"]
    assert syllabify("since", "en") == ["since"]


def test_sung_syllables_en():
    cases = {"money": ["mo", "ney"], "never": ["ne", "ver"], "times": ["times"], "loved": ["loved"],
             "queen": ["queen"], "singing": ["sing", "ing"], "wasted": ["was", "ted"]}
    for word, parts in cases.items():
        assert syllabify(word, "en") == parts, word


def test_language_names():
    assert language_code("Français") == "fr"
    assert language_code("Español") == "es"
    assert language_code("English, Korean (romanized)") == "en"
    assert language_code("Klingon") == "xx"


def test_french_and_spanish_syllables():
    assert syllabify("chanson", "fr") == ["chan", "son"]
    assert syllabify("terre", "fr") == ["terre"]      # mute e sung with its word
    assert syllabify("où", "fr") == ["où"]
    assert syllabify("canción", "es") == ["can", "ción"]


def test_other_scripts():
    assert syllabify("사랑해", "ko") == ["사", "랑", "해"]
    assert syllabify("きょうは", "ja") == ["きょ", "う", "は"]
    assert romanize("Привет") == "privet"
    lyr = prepare("Я тебя люблю", "ru")
    assert [w.syllable_align for w in lyr.words][1] == ["te", "bya"]
