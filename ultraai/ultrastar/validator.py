"""Check a song against the UltraStar spec and USDX's behaviour.

Levels:
- error:   USDX refuses the file or plays it wrongly
- warning: violates the spec / looks broken in game
- info:    style issues (long lines, odd breaks) common even in hand-made files
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .model import Song, parse_number

MAX_LINE_CHARS = 50
MAX_LINE_SECONDS = 10.0


@dataclass
class Issue:
    level: str
    code: str
    message: str
    voice: int | None = None
    line: int | None = None  # 0-based screen-line index

    def __str__(self) -> str:
        where = ""
        if self.voice is not None:
            where = f" [P{self.voice}" + (f" line {self.line + 1}" if self.line is not None else "") + "]"
        return f"{self.level.upper():7} {self.code}{where}: {self.message}"


def validate(song: Song, folder: str | Path | None = None) -> list[Issue]:
    issues: list[Issue] = []
    add = lambda level, code, msg, v=None, ln=None: issues.append(Issue(level, code, msg, v, ln))  # noqa: E731

    for w in song.warnings:
        add("warning", "parse", w)

    # ---- headers -------------------------------------------------------
    try:
        version = song.version
        if version and version[0] >= 2:
            add("error", "version", f"USDX rejects format {song.get('VERSION')} (max 1.x.x)")
    except ValueError as exc:
        add("error", "version", str(exc))
        version = None

    for key in ("TITLE", "ARTIST", "BPM"):
        if not song.has(key):
            add("error", "header", f"missing #{key}")
    if not song.audio:
        add("error", "header", "missing #MP3/#AUDIO")
    for key in ("BPM", "GAP", "VIDEOGAP", "START", "END", "PREVIEWSTART"):
        if song.has(key):
            try:
                value = parse_number(song.get(key) or "")
                if key == "BPM" and value <= 0:
                    add("error", "header", "#BPM must be > 0")
            except ValueError:
                add("error", "header", f"#{key} is not a number: {song.get(key)!r}")
    if version and version >= (1, 0, 0) and song.encoding != "utf-8":
        add("error", "encoding", f"format {song.get('VERSION')} must be UTF-8 (file is {song.encoding})")
    elif song.encoding != "utf-8":
        add("info", "encoding", f"file is {song.encoding}, not UTF-8")
    if song.had_bom:
        add("info", "encoding", "file starts with a UTF-8 BOM")

    if folder is not None:
        folder = Path(folder)
        for key in ("MP3", "AUDIO", "COVER", "BACKGROUND", "VIDEO", "VOCALS", "INSTRUMENTAL"):
            value = song.get(key)
            if value and not _exists_ci(folder, value):
                level = "error" if value == song.audio else "info"
                add(level, "file", f"#{key} file not found: {value}")

    if not song.voices:
        add("error", "body", "song has no notes")
        return issues
    if song.is_duet:
        for v in song.voices:
            if not (song.has(f"P{v}") or song.has(f"DUETSINGERP{v}")):
                add("warning", "duet", f"voice P{v} used but #P{v} name missing")

    # ---- body ----------------------------------------------------------
    try:
        spb = song.seconds_per_beat
    except ValueError:
        spb = None
    for v, lines in song.voices.items():
        prev_end = None
        prev_start = None
        pitches = [n.pitch for ln in lines for n in ln.notes if n.is_pitched]
        if pitches and max(pitches) - min(pitches) > 36:
            add("warning", "pitch", f"pitch range {max(pitches) - min(pitches)} semitones (> 3 octaves)", v)
        for li, line in enumerate(lines):
            for n in line.notes:
                if n.length <= 0:
                    add("warning", "note", f"zero-length note at beat {n.start}", v, li)
                if prev_start is not None and n.start < prev_start:
                    add("error", "order", f"note at beat {n.start} starts before previous note", v, li)
                elif prev_end is not None and n.start < prev_end:
                    add("warning", "overlap", f"note at beat {n.start} overlaps previous (ends {prev_end})", v, li)
                if not n.text.strip():
                    add("info", "text", f"note at beat {n.start} has no text", v, li)
                prev_start, prev_end = n.start, n.end

            text = line.text
            if len(text) > MAX_LINE_CHARS:
                add("info", "long-line", f"{len(text)} chars: {text!r}", v, li)
            if spb and (line.end - line.start) * spb > MAX_LINE_SECONDS:
                add("info", "long-line", f"lasts {(line.end - line.start) * spb:.1f}s: {text!r}", v, li)

            if li < len(lines) - 1:
                nxt = lines[li + 1]
                b = line.break_beat
                if b is None:
                    add("warning", "break", "line break missing", v, li)
                elif b < line.end:
                    add("warning", "break", f"break at {b} lies inside the line (ends {line.end})", v, li)
                elif b > nxt.start:
                    add("warning", "break", f"break at {b} after next line starts ({nxt.start})", v, li)
                elif b == nxt.start:
                    add("info", "break", f"break at {b} equals next note start", v, li)
    return issues


def _exists_ci(folder: Path, name: str) -> bool:
    if (folder / name).exists():
        return True
    lower = name.lower()
    return any(p.name.lower() == lower for p in folder.iterdir())
