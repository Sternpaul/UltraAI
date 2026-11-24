"""Batch conversion: a folder of audio files in, USDX song folders out.

For every audio file:
  metadata  <- file tags (artist/title/year/genre/cover) or "Artist - Title" filename
  lyrics    <- sidecar .lrc/.txt next to the audio, else LRCLIB, else embedded tag lyrics
  language  <- detected from the lyrics text (en / de / el incl. Greeklish)
  output    <- "<out>/<Artist> - <Title>/" with txt, audio, [CO].jpg, [VOC]/[INSTR] stems
A report (report.csv) lists what happened and which songs deserve a manual check.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .lrc import LrcResult, fetch_candidates, parse_lrc
from .lyrics import greeklish

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma"}
LANG_NAMES = {"en": "English", "de": "German", "el": "Greek"}

_DE = {"und", "ich", "du", "nicht", "die", "der", "das", "ist", "mich", "dich", "mir", "dir", "wir",
       "ein", "eine", "auf", "mit", "sie", "es", "zu", "was", "wie", "noch", "nur", "auch", "kein"}
_EN = {"the", "and", "you", "i", "to", "me", "my", "it", "is", "in", "of", "that", "on", "your",
       "love", "be", "we", "all", "for", "don't", "i'm", "with", "so", "what", "can", "just"}
_GREEKLISH = {"kai", "na", "to", "mou", "sou", "pou", "tha", "einai", "ta", "me", "den", "oti",
              "giati", "san", "tin", "ton", "stin", "ap", "pia", "agapi", "zoi", "ego", "esy", "mazi"}


_STOPWORDS = {
    "en": ("en", "English", _EN),
    "de": ("de", "German", _DE),
    "el": ("el", "Greek (romanized)", _GREEKLISH),
    "fr": ("fr", "French", {"je", "tu", "il", "elle", "nous", "vous", "les", "des", "est", "pas", "une",
                            "et", "que", "qui", "dans", "pour", "moi", "toi", "mon", "ma", "c'est", "j'ai",
                            "au", "du", "sur", "avec", "plus", "mais", "ne", "on"}),
    "es": ("es", "Spanish", {"el", "la", "los", "las", "y", "que", "de", "en", "un", "una", "yo", "tu", "mi",
                             "es", "por", "con", "no", "se", "lo", "como", "pero", "mas", "te", "amor", "quiero"}),
    "it": ("it", "Italian", {"il", "la", "che", "di", "e", "non", "un", "una", "io", "tu", "mi", "ti", "per",
                             "con", "sono", "sei", "ma", "come", "piu", "nel", "della", "amore", "gli", "lo"}),
    "nl": ("nl", "Dutch", {"de", "het", "een", "en", "ik", "je", "jij", "niet", "dat", "van", "op", "zijn",
                           "met", "voor", "maar", "wat", "mijn", "ben", "is", "we", "nog", "naar", "ook"}),
    "pt": ("pt", "Portuguese", {"o", "a", "os", "as", "e", "que", "de", "um", "uma", "eu", "voce", "nao",
                                "meu", "minha", "com", "por", "para", "mais", "te", "se", "amor", "do", "da"}),
}
_SCRIPTS = [  # (test, code, #LANGUAGE)
    (lambda c: "Ͱ" <= c <= "Ͽ", "el", "Greek"),
    (lambda c: "Ѐ" <= c <= "ӿ", "ru", "Russian"),
    (lambda c: "가" <= c <= "힣" or "ᄀ" <= c <= "ᇿ", "ko", "Korean"),
    (lambda c: "぀" <= c <= "ヿ", "ja", "Japanese"),
    (lambda c: "一" <= c <= "鿿", "zh", "Chinese"),
    (lambda c: "؀" <= c <= "ۿ", "ar", "Arabic"),
    (lambda c: "֐" <= c <= "׿", "he", "Hebrew"),
    (lambda c: "ऀ" <= c <= "ॿ", "hi", "Hindi"),
    (lambda c: "฀" <= c <= "๿", "th", "Thai"),
]


def detect_language(lines: list[str]) -> tuple[str, str]:
    """Return (code, #LANGUAGE value) from the lyrics text: script first, then common words."""
    import unicodedata

    text = " ".join(lines)
    letters = [c for c in text if c.isalpha()]
    if letters:
        # kana beats CJK ideographs (Japanese uses both)
        if sum("぀" <= c <= "ヿ" for c in letters) / len(letters) > 0.1:
            return "ja", "Japanese"
        for test, code, name in _SCRIPTS:
            if sum(test(c) for c in letters) / len(letters) > 0.3:
                return code, name
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    words = [w.strip(".,!?\"()¡¿").replace("’", "'") for w in plain.split()]
    scores = {code: sum(w in vocab for w in words) for code, (_, _, vocab) in _STOPWORDS.items()}
    best = max(scores, key=lambda k: (scores[k], k == "en"))
    _, code, name = (None, *_STOPWORDS[best][:2])
    return code, name


# ------------------------------------------------------------------ metadata
@dataclass
class Track:
    path: Path
    artist: str
    title: str
    year: str = ""
    genre: str = ""
    cover: bytes | None = None
    tag_lyrics: str = ""
    extra: dict = field(default_factory=dict)


_JUNK = re.compile(r"\s*[\[(](official[^)\]]*|lyrics?[^)\]]*|audio|hd|hq|4k|video|music video|"
                   r"remaster(ed)?[^)\]]*|[A-Za-z0-9_-]{11})[)\]]", re.I)


def clean_tag(text: str) -> str:
    """Strip YouTube IDs and '(Official Video)'-style junk; normalise look-alike slashes."""
    text = text.replace("\u29f8", "/").replace("\u2215", "/").replace("\uff0f", "/")
    prev = None
    while prev != text:
        prev, text = text, _JUNK.sub("", text).strip()
    return text.strip(" -_")


def name_candidates(track: "Track") -> list[tuple[str, str]]:
    """(artist, title) pairs to try for lyrics lookup, best first."""
    out = [(track.artist, track.title)]
    for cand in (track.path.stem, track.path.parent.name):
        m = re.match(r"\s*(.+?)\s+-\s+(.+?)\s*$", clean_tag(cand))
        if m:
            out.append((m.group(1), m.group(2)))
    seen, uniq = set(), []
    for a, t in out:
        key = (a.lower(), t.lower())
        if a and t and key not in seen:
            seen.add(key)
            uniq.append((a, t))
    return uniq


def read_tags(path: Path) -> Track:
    import mutagen

    artist = title = year = genre = lyrics = ""
    cover = None
    try:
        f = mutagen.File(path)
        tags = f.tags if f is not None else None
    except Exception:
        tags = None
    if tags is not None:
        def first(*keys):
            for k in keys:
                if k in tags:
                    v = tags[k]
                    v = v.text if hasattr(v, "text") else v
                    v = v[0] if isinstance(v, list) and v else v
                    if v:
                        return str(v)
            return ""
        artist = first("©ART", "TPE1", "artist", "ARTIST", "aART")
        title = first("©nam", "TIT2", "title", "TITLE")
        year = first("©day", "TDRC", "date", "DATE")[:4]
        genre = first("©gen", "TCON", "genre", "GENRE")
        lyrics = first("©lyr", "lyrics", "LYRICS", "UNSYNCEDLYRICS")
        for k in tags.keys():
            if k.startswith("USLT") and not lyrics:
                lyrics = str(tags[k].text)
            if k.startswith("APIC") and cover is None:
                cover = tags[k].data
        if "covr" in tags and tags["covr"]:
            cover = bytes(tags["covr"][0])
        if hasattr(f, "pictures") and f.pictures and cover is None:  # FLAC
            cover = f.pictures[0].data
    if not artist and " - " in title:  # title tag holds "Artist - Title"
        artist, title = (x.strip() for x in title.split(" - ", 1))
    if artist and title.lower().startswith(artist.lower() + " - "):
        title = title[len(artist) + 3:]
    if not artist or not title:
        for candidate in (path.stem, path.parent.name):
            m = re.match(r"\s*(.+?)\s+-\s+(.+?)\s*$", candidate)
            if m:
                artist, title = artist or m.group(1), title or m.group(2)
                break
        else:
            title = title or path.stem
    return Track(path, clean_tag(artist), clean_tag(title), year, genre, cover, lyrics or "")


def find_lyrics(track: Track) -> tuple[list[LrcResult], str]:
    """Sidecar .lrc/.txt > LRCLIB (several versions) > embedded tag lyrics.

    Returns (candidate lyric versions, source).  With several candidates the transcript
    decides which one fits the recording (see ``generate_sung``).
    """
    for ext in (".lrc", ".txt"):
        side = track.path.with_suffix(ext)
        if side.exists():
            text = side.read_text(encoding="utf-8", errors="replace")
            lines = parse_lrc(text)
            plain = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
            return [LrcResult(track.artist, track.title, 0.0, lines, plain)], f"sidecar{ext}"
    for artist, title in name_candidates(track):
        try:
            res = fetch_candidates(artist, title, track.path)
        except Exception as exc:  # network trouble: fall through to tags
            print(f"   LRCLIB error: {exc}")
            continue
        if res:
            if (artist, title) != (track.artist, track.title):
                track.extra["lookup"] = f"{artist} - {title}"
            return res, "lrclib" + (" (synced)" if res[0].synced else " (plain)")
    if track.tag_lyrics.strip():
        lines = parse_lrc(track.tag_lyrics)
        plain = [ln.strip() for ln in track.tag_lyrics.splitlines() if ln.strip()]
        return [LrcResult(track.artist, track.title, 0.0, lines, plain)], "tags"
    return [], "none"


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .") or "Unknown"


def encode_mp3(src: Path, dst: Path, bitrate: str = "192k") -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                    "-codec:a", "libmp3lame", "-b:a", bitrate, str(dst)], check=True)


# ------------------------------------------------------------------ main loop
REPORT_FIELDS = ["file", "artist", "title", "status", "lyrics_source", "language", "anchored",
                 "lines", "low_confidence_lines", "seconds", "folder", "note"]


def _norm_row(row: dict) -> dict:
    """A row as it is read back from CSV: every field a string, missing ones empty."""
    return {f: "" if row.get(f) is None else str(row.get(f, "")) for f in REPORT_FIELDS}


def load_report(report: Path) -> dict[str, dict]:
    """Existing report rows keyed by the "file" column (a re-run appends, so the last row wins)."""
    if not report.exists() or report.stat().st_size == 0:
        return {}
    with open(report, newline="", encoding="utf-8") as f:
        return {r["file"]: r for r in csv.DictReader(f) if r.get("file")}


def append_report_row(report: Path, row: dict, existing: dict[str, dict]) -> bool:
    """Append one song's row to report.csv — O(1) per song instead of rewriting every row.

    The header is written only when the file is new.  ``existing`` is the report as
    returned by load_report() and is updated in place.  A row identical to the stored
    one for the same song is not appended again, so resuming never duplicates rows.
    Returns True when a row was written.
    """
    key = str(row.get("file", ""))
    norm = _norm_row(row)
    if existing.get(key) == norm:
        return False
    fresh = not report.exists() or report.stat().st_size == 0
    with open(report, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        if fresh:
            w.writeheader()
        w.writerow(row)
    existing[key] = norm
    return True


def convert(track: Track, out_root: Path, separation: str, lead: str | None, greek_script: bool,
            stems: bool, method: str = "m3") -> dict:
    from .audio import lead_vocals, separate
    from .generate import generate_from_lrc, generate_sung
    from .ultrastar.validator import validate
    from .ultrastar.writer import write

    row = {"file": str(track.path), "artist": track.artist, "title": track.title}
    cands, source = find_lyrics(track)
    cands = [c for c in cands if c.texts]
    row["lyrics_source"] = source
    if not cands:
        return {**row, "status": "skipped", "note": "no lyrics found (add a .lrc/.txt next to the audio)"}

    code, lang_name = detect_language(cands[0].texts)
    if code == "el" and lang_name == "Greek" and not greek_script:
        for lrc in cands:
            for ln in lrc.lines:
                ln.text = greeklish(ln.text)
            lrc.plain = [greeklish(t) for t in lrc.plain]
        lang_name = "Greek (romanized)"
    row["language"] = lang_name

    name = safe_name(f"{track.artist} - {track.title}")
    folder = out_root / name
    folder.mkdir(parents=True, exist_ok=True)
    audio_name = f"{name}{track.path.suffix.lower()}"
    if not (folder / audio_name).exists():
        shutil.copy2(track.path, folder / audio_name)
    meta = {"ARTIST": track.artist, "TITLE": track.title, "LANGUAGE": lang_name}
    if track.year:
        meta["YEAR"] = track.year
    if track.genre:
        meta["GENRE"] = track.genre
    if track.cover:
        cover = f"{name} [CO].jpg"
        (folder / cover).write_bytes(track.cover)
        meta["COVER"] = cover
    if stems:
        vocals, instr = separate(track.path, separation)
        meta["VOCALS"], meta["INSTRUMENTAL"] = f"{name} [VOC].mp3", f"{name} [INSTR].mp3"
        if not (folder / meta["VOCALS"]).exists():
            encode_mp3(vocals, folder / meta["VOCALS"])
            encode_mp3(instr, folder / meta["INSTRUMENTAL"])
    if lead:
        lead_vocals(separate(track.path, separation)[0], lead)

    if lead:
        import os

        from . import config
        os.environ[config.env_of("lead")] = lead
    if method == "m3":
        song, info = generate_sung(track.path, cands, code, meta, separation=separation)
        source += f", version {info['candidate'] + 1}/{info['candidates']}, structure {info['structure']}"
        row["lyrics_source"] = source
    else:
        song, info = generate_from_lrc(track.path, cands[0], code, meta, separation=separation)
    song.set("MP3", audio_name)
    song.set("AUDIO", audio_name)
    write(song, folder / f"{name}.txt")
    import json
    (folder / f"{name}.ultraai.json").write_text(json.dumps(
        {"lyrics_source": source, "anchored": info.get("anchored"), "structure": info.get("structure"),
         "lines": info.get("lines", []), "issues": info.get("issues", []),
         "words": info.get("words", []), "risk_model": info.get("risk_model")},
        indent=1, ensure_ascii=False), encoding="utf-8")
    errors = [i for i in validate(song, folder) if i.level == "error"]
    row.update(status="error" if errors else "ok", folder=str(folder), anchored=info.get("anchored"),
               lines=len(song.lines()), low_confidence_lines=info.get("low_confidence_lines", ""),
               note="; ".join(str(e) for e in errors))
    return row


def run_batch(src: Path, out_root: Path, separation: str = "melband", lead: str | None = "kara2",
              greek_script: bool = False, stems: bool = True, force: bool = False) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in Path(src).rglob("*") if p.suffix.lower() in AUDIO_EXT)
    report = out_root / "report.csv"
    existing = load_report(report)
    done = set() if force else {k for k, r in existing.items() if r.get("status") == "ok"}
    print(f"{len(files)} audio files, {len(done)} already converted")
    for k, path in enumerate(files, 1):
        if str(path) in done:
            continue
        track = read_tags(path)
        print(f"[{k}/{len(files)}] {track.artist} - {track.title}")
        t0 = time.time()
        try:
            row = convert(track, out_root, separation, lead, greek_script, stems)
        except Exception as exc:
            traceback.print_exc()
            row = {"file": str(path), "artist": track.artist, "title": track.title, "status": "error",
                   "note": repr(exc)}
        row["seconds"] = round(time.time() - t0)
        print(f"   -> {row['status']} ({row.get('lyrics_source', '')}, {row.get('language', '')}, "
              f"anchored={row.get('anchored', '')}, {row['seconds']} s) {row.get('note', '')}")
        append_report_row(report, row, existing)
    return report
