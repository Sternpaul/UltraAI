"""Lyrics preparation: clean text, romanise for the aligner, split into syllables.

The *display* text (what appears on screen) is never changed except for cleanup of
markup like "[Chorus]". The aligner only sees a romanised, lowercase a-z/' version.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pyphen

# ---------------------------------------------------------------- romanisation / Greeklish
# ONE canonical Greek transliteration, shared by romanize() (lowercase a-z, the aligner
# alphabet) and greeklish() (case-preserving, the USDB display text).  χ -> "ch" (not "x"):
# eval/metrics.py folds ch/kh -> x when comparing, so both spellings score the same.
_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "i", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}
_GREEK_PAIRS = {"ου": "ou", "αι": "ai", "ει": "ei", "οι": "oi", "αυ": "av", "ευ": "ev",
                "μπ": "b", "ντ": "nt", "γκ": "gk", "γγ": "gg"}
_SPECIAL = {"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "ł": "l", "đ": "d", "þ": "th",
            "’": "'", "‘": "'", "`": "'", "´": "'"}


_uroman = None


def _is_other_script(c: str) -> bool:
    """A letter that is neither Latin nor Greek (Cyrillic, Hangul, CJK, Arabic, ...)."""
    if not c.isalpha():
        return False
    name = unicodedata.name(c, "")
    return not (name.startswith("LATIN") or name.startswith("GREEK"))


def _uromanize(text: str) -> str:
    """Universal romanizer (the one MMS_FA was trained with) for scripts we don't map ourselves."""
    global _uroman
    if _uroman is None:
        import uroman

        _uroman = uroman.Uroman()
    return _uroman.romanize_string(text)


def greeklish(text: str) -> str:
    """Transliterate Greek script the way USDB song files usually do ("Πού πας" -> "Pou pas").

    The canonical Greek -> Latin function (batch.py used to carry a second one with
    χ -> "x"; it now imports this one).
    """
    out, i = [], 0
    base = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    while i < len(base):
        two = base[i:i + 2].lower()
        if two in _GREEK_PAIRS:
            rep = _GREEK_PAIRS[two]
            i += 2
            src = base[i - 2]
        else:
            c = base[i]
            rep = _GREEK.get(c.lower(), c)
            src = c
            i += 1
        out.append(rep.capitalize() if src.isupper() and rep != src else rep)
    return "".join(out)


def romanize(text: str) -> str:
    """Lowercase a-z and apostrophe only (the MMS_FA alphabet)."""
    if any(_is_other_script(c) for c in text):
        text = _uromanize(text)
    out = []
    for ch in text.lower():
        ch = _SPECIAL.get(ch, ch)
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        base = "".join(_GREEK.get(c, c) for c in base)
        out.append("".join(c for c in base if "a" <= c <= "z" or c == "'"))
    return "".join(out)


# ---------------------------------------------------------------- cleanup
_SECTION = re.compile(r"^\s*\[[^\]]*\]\s*$")                 # "[Chorus]" on its own line
_PAREN = re.compile(r"\(([^()]*)\)")
_INLINE_TAG = re.compile(r"\[[^\]]*\]")                      # "[Verse 2: X]" inline
_REPEAT = re.compile(r"\s*[(\[]?\s*[x×]\s*(\d+)\s*[)\]]?\s*$", re.IGNORECASE)  # "(x2)"


def _words(text: str) -> set[str]:
    return {romanize(w) for w in text.split()} - {""}


def _drop_tail_overlap(inner: str, previous: str) -> str:
    """'(The world is like ...)' after '... taste for the world' -> 'is like ...'."""
    words, prev = inner.split(), [romanize(w) for w in previous.split()]
    for k in range(min(len(words) - 1, len(prev), 4), 0, -1):
        if [romanize(w) for w in words[:k]] == prev[-k:]:
            return " ".join(words[k:])
    return inner


def strip_echoes(line: str, previous: str = "") -> str:
    """Drop "(...)" echoes of words just sung ("Take on me (take on me)"), keep other "(...)" text."""
    whole = re.fullmatch(r"\s*\(([^()]*)\)\s*", line)
    if whole:  # a whole backing line: keep it, minus a repeat of the previous line's ending
        return _drop_tail_overlap(whole.group(1).strip(), previous)
    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        outside = _PAREN.sub(" ", line)
        if inner and _words(inner) <= (_words(outside) | _words(previous)) and _PAREN.sub("", line).strip():
            return " "
        return " " + inner + " "
    return re.sub(r"\s+", " ", _PAREN.sub(repl, line)).strip()


def clean_lines(raw: str | list[str], drop_echoes: bool = True) -> list[str]:
    """Remove section headers / inline tags, expand trailing "(x2)", drop echoes and empties."""
    lines = raw.splitlines() if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for line in lines:
        line = unicodedata.normalize("NFC", line).strip()
        if not line or _SECTION.match(line):
            continue
        line = _INLINE_TAG.sub("", line).strip()
        repeat = 1
        m = _REPEAT.search(line)
        if m and m.start() > 0:
            repeat = max(1, min(8, int(m.group(1))))
            line = line[: m.start()].strip()
        if drop_echoes:
            line = strip_echoes(line, out[-1] if out else "")
        line = re.sub(r"(?<=\w)-(?=\w)", " ", line)   # "Ah-ah-ah" / "well-known" -> separate words
        line = re.sub(r"\s+", " ", line)
        if line:
            out.extend([line] * repeat)
    return out


# ---------------------------------------------------------------- syllables
def _pyphen_langs() -> dict[str, str]:
    """ISO 639-1 -> pyphen dictionary for every language pyphen ships."""
    pref = {"en": "en_US", "de": "de_DE", "el": "el_GR", "pt": "pt_PT", "fr": "fr", "nl": "nl_NL"}
    out: dict[str, str] = {}
    for name in sorted(pyphen.LANGUAGES):
        out.setdefault(name.split("_")[0], name)
    out.update({k: v for k, v in pref.items() if v in pyphen.LANGUAGES})
    return out


_PYPHEN_LANG = _pyphen_langs()
_VOWELS = set("aeiouyäöüáéíóúàèìòùâêîôûëïœãõαεηιουωάέήίόύώϊϋΐΰаеёиоуыэюяіїє")
# vowel teams that form one nucleus (romanised spelling)
_TEAMS = {
    "en": ["eau", "ai", "ay", "ea", "ee", "ei", "ey", "ie", "oa", "oe", "oi", "oo", "ou", "ow", "oy", "ue", "ui"],
    "de": ["aa", "ai", "au", "äu", "ee", "ei", "eu", "ie", "oo"],
    "el": ["ai", "ei", "oi", "ou", "au", "eu", "ui", "αι", "ει", "οι", "ου", "αυ", "ευ", "υι"],
    "fr": ["oeu", "œu", "eau", "aie", "au", "ai", "ei", "eu", "ou", "où", "oi", "oe", "œ", "ay", "ey", "oy",
           "ue", "ui", "ie", "iè", "ié", "ui"],
    "es": ["ia", "ie", "io", "iu", "ua", "ue", "ui", "uo", "ai", "ay", "ei", "ey", "oi", "oy", "au", "eu", "ió", "ué", "ié"],
    "it": ["ia", "ie", "io", "iu", "ua", "ue", "uo", "ai", "ei", "oi", "au", "uò", "iò", "iù"],
    "pt": ["ai", "ei", "oi", "ui", "au", "eu", "ou", "ão", "ãe", "õe", "ia", "ie", "io", "ua", "ue"],
    "nl": ["aai", "ooi", "oei", "eeu", "ieu", "aa", "ee", "oo", "uu", "ie", "oe", "ou", "ui", "ei", "ij", "eu", "au"],
}
_SYL_CACHE: dict[str, pyphen.Pyphen] = {}


def _nuclei(word: str, lang: str) -> list[tuple[int, int]]:
    """Return [start, end) spans of vowel nuclei."""
    low = word.lower()
    teams = sorted(_TEAMS.get(lang, _TEAMS["en"]), key=len, reverse=True)
    spans, i = [], 0
    while i < len(low):
        if low[i] == "u" and i > 0 and low[i - 1] == "q" and i + 1 < len(low) and low[i + 1] in _VOWELS:
            i += 1  # 'qu' + vowel: the u is part of the consonant
            continue
        if low[i] in _VOWELS and not (low[i] == "y" and i == 0 and len(low) > 1 and low[1] in _VOWELS):
            j = i + 1
            for t in teams:
                if low.startswith(t, i):
                    j = i + len(t)
                    break
            spans.append((i, j))
            i = j
        else:
            i += 1
    if lang == "en" and len(spans) > 1 and low.endswith("e") and spans[-1] == (len(low) - 1, len(low)) \
            and not low.endswith(("le", "ee", "ye")):
        spans.pop()  # silent final e ("love", "fire")
    return spans


def _rule_split(word: str, lang: str) -> list[str]:
    """Vowel-nucleus syllabifier: V-CV, VC-CV, VC-CCV."""
    spans = _nuclei(word, lang)
    if len(spans) < 2:
        return [word]
    cuts = []
    for (_, end), (start, _) in zip(spans, spans[1:]):
        cons = start - end
        cuts.append(end if cons <= 1 else end + 1)
    parts, prev = [], 0
    for c in cuts:
        parts.append(word[prev:c])
        prev = c
    parts.append(word[prev:])
    return [p for p in parts if p]


def _has_nucleus(part: str, lang: str) -> bool:
    return bool(_nuclei(part, lang)) and not (lang == "en" and part.lower() in ("y",))


def _merge_vowelless(parts: list[str], lang: str) -> list[str]:
    """pyphen with left=1 may produce 's-ince' / 'y-ou': glue vowel-less parts to a neighbour."""
    out: list[str] = []
    for k, p in enumerate(parts):
        letters = "".join(c for c in p if c.isalpha())
        ok = _has_nucleus(letters, lang) and not (
            letters.lower() == "y" or (letters.lower().startswith("y") and len(letters) == 1))
        if out and not ok:
            out[-1] += p
        elif not ok and k + 1 < len(parts):
            parts[k + 1] = p + parts[k + 1]
        else:
            out.append(p)
    # a leading 'y' followed by a vowel part ("y-ou") is a consonant: merge
    if len(out) > 1 and out[0].lower() == "y":
        out[1] = out[0] + out[1]
        out = out[1:]
    return out or parts


_VOW = set("aeiouyäöüáéíóúàèìòù")
_DIGRAPHS = ("sch", "sh", "ch", "th", "ph", "wh", "gh")


def _merge_silent_e(parts: list[str]) -> list[str]:
    """English: 'ti-mes', 'pla-ce', 'lo-ved', 'que-en' -> one syllable ('wast-ed', 'lit-tle' stay)."""
    out = list(parts)
    k = 1
    while k < len(out):
        cur = "".join(c for c in out[k].lower() if c.isalpha())
        prev = "".join(c for c in out[k - 1].lower() if c.isalpha())
        silent = (k == len(out) - 1 and re.fullmatch(r"[^aeiouy]*e[sd]?", cur)
                  and not re.fullmatch(r"[td]ed|[^aeiouy]*le|[^aeiouy]*les", cur)
                  and not (cur == "ed" and prev[-1:] in ("t", "d"))
                  and not re.fullmatch(r"(c|s|sh|ch|x|z|g)es", cur))
        team = prev[-1:] in _VOW and cur[:1] in _VOW and (prev[-1:] + cur[:1]) in ("ee", "oo", "ea", "ou", "ai")
        if silent or team:
            out[k - 1] += out[k]
            del out[k]
        else:
            k += 1
    return out


def _merge_mute_e_fr(parts: list[str]) -> list[str]:
    """French files sing the final mute e with its word: 'ter-re' -> 'terre', 'pé-ni-ble' -> 'pé-nible'."""
    if len(parts) < 2:
        return parts
    last = "".join(c for c in parts[-1].lower() if c.isalpha() or c == "’" or c == "'")
    if re.fullmatch(r"[^aeiouyéèêàâîôûëïœ]*e(s|nt)?", last):
        return parts[:-2] + [parts[-2] + parts[-1]]
    return parts


def _sung_boundaries(parts: list[str], lang: str) -> list[str]:
    """Move a consonant to the next syllable when that one starts with a vowel: 'mon-ey' -> 'mo-ney'."""
    out = list(parts)
    for k in range(1, len(out)):
        left, right = out[k - 1], out[k]
        if (lang == "en" and left.lower().endswith("n") and right[:1].lower() == "g"
                and right[1:2].lower() in _VOW):  # 'sin-ging' -> 'sing-ing'
            out[k - 1], out[k] = left + right[0], right[1:]
            continue
        if not right[:1].isalpha() or right[:1].lower() not in _VOW:
            continue
        low = left.lower()
        if low.endswith("ng") or low.endswith("x"):
            continue  # 'sing-ing' keeps its ng
        unit = next((d for d in _DIGRAPHS if low.endswith(d)), None)
        if unit is None:
            unit = low[-1:] if low[-1:].isalpha() and low[-1:] not in _VOW else None
        if unit is None:
            continue
        rest = left[: len(left) - len(unit)]
        if not _nuclei(rest, lang):
            continue
        if lang == "en" and unit == "ck":
            continue
        out[k - 1], out[k] = rest, left[len(rest):] + right
    return out


def _is_syllabic_char(c: str) -> bool:
    """Scripts where one character is one sung syllable: Hangul, kana, CJK ideographs."""
    o = ord(c)
    return (0xAC00 <= o <= 0xD7A3 or 0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF
            or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF)


_SMALL_KANA = set("ゃゅょぁぃぅぇぉっャュョァィゥェォッー")


def _split_syllabic(word: str) -> list[str]:
    """One part per syllabic character; small kana / long-vowel marks and punctuation join the previous part."""
    parts: list[str] = []
    for c in word:
        if parts and (c in _SMALL_KANA or not c.isalpha() or not _is_syllabic_char(c) and not parts[-1][-1:].isspace()
                      and not _is_syllabic_char(parts[-1][-1])):
            parts[-1] += c
        elif parts and not _is_syllabic_char(c):
            parts[-1] += c
        else:
            parts.append(c)
    return parts or [word]


def syllabify(word: str, lang: str = "en") -> list[str]:
    """Split a display word (may contain punctuation) into syllables.

    pyphen first (left=right=1, like hand-made files: "a-gain"); if it finds nothing but the
    word has several vowel nuclei ("singing", Greeklish words), fall back to rules.
    """
    if not any(c.isalpha() for c in word):
        return [word]
    if any(_is_syllabic_char(c) for c in word):
        return _split_syllabic(word)
    code = _PYPHEN_LANG.get(lang)
    is_greek_script = any("Ͱ" <= c <= "Ͽ" for c in word)
    parts = [word]
    if code and not (lang == "el" and not is_greek_script):  # Greeklish -> rules only
        dic = _SYL_CACHE.setdefault(code, pyphen.Pyphen(lang=code, left=1, right=1))
        parts = dic.inserted(word, hyphen="\x00").split("\x00")
    if len(parts) == 1 and (code is None or lang in ("en", "el", "fr", "es", "nl", "pt")):
        # pyphen misses e.g. English '-ing' forms; for other languages trust it
        parts = _rule_split(word, lang)
    if len(parts) > 1:
        parts = _merge_vowelless(parts, lang)
    if len(parts) > 1 and lang == "en":
        parts = _merge_silent_e(parts)
    if len(parts) > 1 and lang == "fr":
        parts = _merge_mute_e_fr(parts)
    if len(parts) > 1:
        parts = _sung_boundaries(parts, lang)
    # never leave a part without letters (e.g. trailing punctuation) on its own
    merged: list[str] = []
    for p in parts:
        if merged and not any(c.isalpha() for c in p):
            merged[-1] += p
        elif merged and not any(c.isalpha() for c in merged[-1]):
            merged[-1] += p
        else:
            merged.append(p)
    return merged


# ---------------------------------------------------------------- data model
@dataclass
class LyricWord:
    display: str                 # as shown, e.g. "Don't,"
    syllables: list[str]         # display parts, concatenated == display
    line: int                    # index into the lyric lines
    align: str = ""              # romanised for the aligner, e.g. "don't"
    syllable_align: list[str] = field(default_factory=list)


@dataclass
class Lyrics:
    lines: list[str]
    words: list[LyricWord]
    language: str


# #LANGUAGE values seen in song files (English, native and German names) -> ISO 639-1
LANGUAGE_CODES = {
    "english": "en", "englisch": "en", "german": "de", "deutsch": "de", "greek": "el",
    "griechisch": "el", "ελληνικά": "el", "french": "fr", "français": "fr", "francais": "fr",
    "französisch": "fr", "spanish": "es", "español": "es", "espanol": "es", "spanisch": "es",
    "italian": "it", "italiano": "it", "italienisch": "it", "dutch": "nl", "nederlands": "nl",
    "niederländisch": "nl", "portuguese": "pt", "português": "pt", "portugues": "pt",
    "finnish": "fi", "suomi": "fi", "swedish": "sv", "svenska": "sv", "schwedisch": "sv",
    "norwegian": "nb", "norsk": "nb", "danish": "da", "dansk": "da", "polish": "pl", "polski": "pl",
    "czech": "cs", "hungarian": "hu", "magyar": "hu", "romanian": "ro", "croatian": "hr",
    "serbian": "sr", "slovenian": "sl", "slovak": "sk", "russian": "ru", "русский": "ru",
    "ukrainian": "uk", "turkish": "tr", "türkçe": "tr", "catalan": "ca", "català": "ca",
    "latin": "la", "irish": "ga", "welsh": "cy", "icelandic": "is", "estonian": "et",
    "latvian": "lv", "lithuanian": "lt", "bulgarian": "bg", "korean": "ko", "japanese": "ja",
    "chinese": "zh", "mandarin": "zh", "cantonese": "zh", "hindi": "hi", "arabic": "ar",
    "hebrew": "he", "persian": "fa", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "tagalog": "tl", "swahili": "sw", "scat": "en",
}


def language_code(name: str | None) -> str:
    """'English, Korean (romanized)' -> 'en' (first named language); unknown -> 'xx'."""
    if not name:
        return "en"
    first = re.split(r"[,/;&+]| and | und ", name.lower())[0].strip()
    for key, code in LANGUAGE_CODES.items():
        if key in first:
            return code
    if re.fullmatch(r"[a-z]{2}", first):
        return first
    return "xx"


# ---------------------------------------------------------------- numerals -> words
# our ISO 639-1 code -> num2words converter name (num2words has no 'nb', only 'no')
_N2W_LANG_ALIAS = {"nb": "no"}
_CURRENCY = re.compile(r"[$€£¥₹]")
_INT_CORE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")      # "1,000" or "22"
_ORD_CORE = re.compile(r"(\d+)(?:st|nd|rd|th)", re.IGNORECASE)  # "21st"
_DEC_CORE = re.compile(r"(\d+)\.(\d+)")                # "3.5"
_n2w_cache: dict[tuple[int, str, str], str | None] = {}


def _n2w_lang(lang: str) -> str | None:
    """num2words converter name for our ISO code, or None when num2words has no such language."""
    try:
        from num2words import CONVERTER_CLASSES
    except ImportError:
        return None
    code = _N2W_LANG_ALIAS.get(lang, lang)
    return code if code in CONVERTER_CLASSES else None


def _n2w(n: int, nlang: str, to: str) -> str | None:
    key = (n, nlang, to)
    if key not in _n2w_cache:
        try:
            from num2words import num2words
            _n2w_cache[key] = num2words(n, lang=nlang, to=to) or None
        except Exception:  # unsupported lang or unsupported 'to' (e.g. no year reading)
            _n2w_cache[key] = None
    return _n2w_cache[key]


def _expand_token(tok: str, nlang: str) -> str:
    """One whitespace-free token -> spoken words, or unchanged when it is not a plain numeral."""
    if _CURRENCY.search(tok):  # "$5" stays "$5"
        return tok
    m = re.fullmatch(r"(\W*)(.*?)(\W*)", tok)
    lead, core, tail = m.group(1), m.group(2), m.group(3)
    out = None
    if _DEC_CORE.fullmatch(core):
        whole = _n2w(int(_DEC_CORE.fullmatch(core).group(1)), nlang, "cardinal")
        frac = _DEC_CORE.fullmatch(core).group(2)
        if whole:
            out = whole + " point " + " ".join(
                _n2w(int(d), nlang, "cardinal") or d for d in frac)
    elif _ORD_CORE.fullmatch(core):
        out = _n2w(int(_ORD_CORE.fullmatch(core).group(1)), nlang, "ordinal")
    elif _INT_CORE.fullmatch(core):
        v = int(core.replace(",", ""))
        if 0 <= v <= 9999:
            if "," not in core and 1000 <= v <= 2099:  # "1999" is usually sung as a year
                out = _n2w(v, nlang, "year") or _n2w(v, nlang, "cardinal")
            else:
                out = _n2w(v, nlang, "cardinal")
    return lead + out + tail if out else tok


def expand_numbers(text: str, lang: str = "en") -> str:
    """Rewrite numeral tokens as the words a singer sings them ("at 22" -> "at twenty-two").

    Whisper hears the sung words, so lyrics used for processing (and the output .txt) must
    carry words too, or a digit lands on one note while its sung syllables stay empty.
    Per token: integers 0..9999 (optional thousand separators) -> cardinal words, plain
    1000..2099 prefer the year reading ("1999" -> "nineteen ninety-nine") when num2words
    supports it, ordinals ("1st") -> ordinal words, decimals ("3.5" -> "three point five");
    everything else ("$5", "COVID-19", "3D") is unchanged.  ``lang`` is our ISO 639-1 code
    (or a #LANGUAGE value); unsupported languages return the text unchanged.  Whitespace
    and line structure are preserved exactly.
    """
    code = lang if re.fullmatch(r"[a-z]{2}", lang or "") else language_code(lang)
    nlang = _n2w_lang(code)
    if nlang is None:
        return text
    return re.sub(r"\S+", lambda m: _expand_token(m.group(0), nlang), text)


def prepare(raw: str | list[str], language: str = "en") -> Lyrics:
    lines = clean_lines(raw)
    words: list[LyricWord] = []
    for li, line in enumerate(lines):
        for token in line.split():
            align = romanize(token)
            if not align.strip("'"):
                # pure punctuation ("-", "…"): glue to previous word for display
                if words and words[-1].line == li:
                    words[-1].display += " " + token
                    words[-1].syllables[-1] += " " + token
                continue
            syl = syllabify(token, language)
            syl_align = [romanize(s) for s in syl]
            # merge syllables that romanise to nothing into a neighbour
            k = 0
            while k < len(syl):
                if not syl_align[k] and len(syl) > 1:
                    j = k - 1 if k > 0 else k + 1
                    if j < k:
                        syl[j] += syl[k]
                        syl_align[j] += syl_align[k]
                    else:
                        syl[j] = syl[k] + syl[j]
                        syl_align[j] = syl_align[k] + syl_align[j]
                    del syl[k], syl_align[k]
                else:
                    k += 1
            words.append(LyricWord(token, syl, li, align, syl_align))
    return Lyrics(lines, words, language)
