"""Read UltraStar .txt files, mirroring USDX's parser (src/base/USong.pas).

Notable USDX behaviour reproduced here:
- note lines are whitespace-tokenised; after the pitch exactly ONE whitespace
  character is consumed, everything after it is the syllable text (so
  ": 8 6 14  been" has the text " been").
- the header section ends at the first line not starting with '#'.
- '#RELATIVE:yes' (unversioned only) is converted to absolute beats.
- everything after 'E' is ignored (as USDX does), except our own '#ULTRAAI-AUTOFIX:'
  trailer there, which marks double-check auto-corrected notes; 'B' lines are ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import NOTE_KINDS, Line, Note, Song

_WS = " \t"
_BOM = b"\xef\xbb\xbf"


class ParseError(ValueError):
    pass


def decode(data: bytes) -> tuple[str, str, bool]:
    """Return (text, encoding, had_bom). Falls back to the #ENCODING header or CP1252."""
    had_bom = data.startswith(_BOM)
    if had_bom:
        data = data[len(_BOM):]
    try:
        return data.decode("utf-8"), "utf-8", had_bom
    except UnicodeDecodeError:
        pass
    m = re.search(rb"^#ENCODING:\s*([^\r\n]+)", data, re.IGNORECASE | re.MULTILINE)
    enc = "cp1252"
    if m:
        name = m.group(1).strip().decode("ascii", "ignore").lower().replace("-", "")
        # a file that says UTF-8 but is not valid UTF-8 is (in practice) CP1252
        enc = {"cp1250": "cp1250", "cp1252": "cp1252"}.get(name, "cp1252")
    return data.decode(enc, errors="replace"), enc, had_bom


def _tokens(line: str, count: int) -> tuple[list[str], str]:
    """Split ``count`` whitespace-separated params, then return the rest after one separator."""
    params: list[str] = []
    pos = 0
    n = len(line)
    for _ in range(count):
        while pos < n and line[pos] in _WS:
            pos += 1
        start = pos
        while pos < n and line[pos] not in _WS:
            pos += 1
        if start == pos:
            raise ParseError(f"expected {count} parameters in {line!r}")
        params.append(line[start:pos])
        if pos < n:
            pos += 1  # USDX skips exactly one trailing whitespace character
    return params, line[pos:]


def parse(text: str) -> Song:
    song = Song()
    raw_lines = text.splitlines()
    i = 0

    # ---- header -------------------------------------------------------
    while i < len(raw_lines):
        line = raw_lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if not stripped.startswith("#"):
            break
        body = stripped[1:]
        if ":" not in body:
            song.warnings.append(f"line {i + 1}: header without ':' ignored: {stripped!r}")
        else:
            key, value = body.split(":", 1)
            song.headers.append((key.strip().upper(), value.strip()))
        i += 1

    relative = (song.get("RELATIVE", "") or "").strip().upper() == "YES"
    version = None
    try:
        version = song.version
    except ValueError as exc:
        song.warnings.append(str(exc))
    if version and version >= (1, 0, 0):
        relative = False  # USDX ignores RELATIVE for format >= 1.0.0
    song.was_relative = relative

    # ---- body ---------------------------------------------------------
    voice = 1
    rel: dict[int, int] = {}
    current: dict[int, Line] = {}

    def cur(v: int) -> Line:
        if v not in current:
            current[v] = Line()
            song.voices.setdefault(v, []).append(current[v])
        return current[v]

    after_e = False
    for lineno in range(i, len(raw_lines)):
        line = raw_lines[lineno].rstrip("\r\n")
        stripped = line.strip()
        if not stripped:
            continue
        tag = stripped[0]
        where = f"line {lineno + 1}"
        try:
            if tag == "E":
                after_e = True
                continue
            if after_e:
                # USDX ignores everything after 'E'; only our own trailer is read there
                if stripped[:17].upper() == "#ULTRAAI-AUTOFIX:":
                    parts = stripped.split(":")
                    if len(parts) == 3:
                        v = int(parts[1])
                        notes = [n for ln in song.voices.get(v, []) for n in ln.notes]
                        for tok in parts[2].split(","):
                            idx = int(tok)
                            if 0 <= idx < len(notes):
                                notes[idx].auto_fixed = True
                continue
            if tag == "P":
                m = re.fullmatch(r"P\s*(\d+)", stripped)
                if not m:
                    raise ParseError(f"bad voice change {stripped!r}")
                voice = int(m.group(1))
                if voice == 3:  # legacy "both singers" marker; treat as P1
                    song.warnings.append(f"{where}: legacy P3 treated as P1")
                    voice = 1
                continue
            if tag in NOTE_KINDS:
                (_, start, length, pitch), lyric = _tokens(line.lstrip(_WS), 4)
                start_i = int(start) + rel.get(voice, 0)
                note = Note(tag, start_i, int(length), int(pitch), lyric)
                if note.length == 0:
                    song.warnings.append(f"{where}: zero-length note (USDX turns it into freestyle)")
                cur(voice).notes.append(note)
                continue
            if tag == "-":
                params = stripped[1:].split()
                if not params:
                    raise ParseError("line break without beat")
                beat = int(params[0]) + rel.get(voice, 0)
                if relative:
                    offset = int(params[1]) if len(params) > 1 else int(params[0])
                    rel[voice] = rel.get(voice, 0) + offset
                line_obj = cur(voice)
                if not line_obj.notes:
                    song.warnings.append(f"{where}: line break without notes ignored")
                    continue
                line_obj.break_beat = beat
                del current[voice]
                continue
            if tag == "B":
                song.warnings.append(f"{where}: variable BPM line ignored (as USDX does)")
                continue
            song.warnings.append(f"{where}: unknown line ignored: {stripped!r}")
        except (ParseError, ValueError) as exc:
            song.warnings.append(f"{where}: {exc}")

    # drop empty trailing lines
    for v, lines in list(song.voices.items()):
        song.voices[v] = [ln for ln in lines if ln.notes]
        if not song.voices[v]:
            del song.voices[v]
    return song


def read_bytes(data: bytes) -> Song:
    text, encoding, had_bom = decode(data)
    song = parse(text)
    song.encoding = encoding
    song.had_bom = had_bom
    return song


def read(path: str | Path) -> Song:
    return read_bytes(Path(path).read_bytes())
