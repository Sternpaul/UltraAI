"""Write UltraStar .txt files (format 1.0.0 by default, UTF-8, LF, absolute beats)."""

from __future__ import annotations

import copy
from pathlib import Path

from .model import Song

# Canonical header order for files we generate. Unknown headers follow in their original order.
HEADER_ORDER = [
    "VERSION", "TITLE", "ARTIST", "LANGUAGE", "EDITION", "GENRE", "YEAR", "CREATOR",
    "TAGS", "RENDITION", "MP3", "AUDIO", "VOCALS", "INSTRUMENTAL", "COVER", "BACKGROUND",
    "VIDEO", "VIDEOGAP", "BPM", "GAP", "START", "END", "PREVIEWSTART",
    "MEDLEYSTARTBEAT", "MEDLEYENDBEAT", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9",
    "PROVIDEDBY", "AUDIOURL", "VIDEOURL", "COVERURL", "BACKGROUNDURL", "COMMENT",
]

# Headers that USDX ignores (or that are deprecated) once #VERSION >= 1.0.0.
DEPRECATED_IN_V1 = {"ENCODING", "RELATIVE", "NOTESGAP", "RESOLUTION", "DUETSINGERP1",
                    "DUETSINGERP2", "AUTHOR", "PREVIEW"}


def upgrade_to_v1(song: Song, version: str = "1.0.0") -> Song:
    """Return a copy converted to format 1.x: renamed/dropped legacy headers, #AUDIO added."""
    out = copy.deepcopy(song)
    for old, new in (("DUETSINGERP1", "P1"), ("DUETSINGERP2", "P2"), ("AUTHOR", "CREATOR"),
                     ("PREVIEW", "PREVIEWSTART")):
        if out.has(old) and not out.has(new):
            out.set(new, out.get(old))
    out.headers = [(k, v) for k, v in out.headers if k not in DEPRECATED_IN_V1]
    out.set("VERSION", version)
    if out.has("MP3") and not out.has("AUDIO"):
        out.set("AUDIO", out.get("MP3"))
    if out.has("AUDIO") and not out.has("MP3"):
        out.set("MP3", out.get("AUDIO"))  # keeps very old games working
    out.was_relative = False
    return out


def _ordered_headers(song: Song) -> list[tuple[str, str]]:
    rank = {k: i for i, k in enumerate(HEADER_ORDER)}
    known = [(k, v) for k, v in song.headers if k in rank]
    unknown = [(k, v) for k, v in song.headers if k not in rank]
    known.sort(key=lambda kv: rank[kv[0]])
    return known + unknown


def dumps(song: Song, reorder: bool = True, newline: str = "\n") -> str:
    """Serialise ``song``. Body is always written with absolute beats."""
    out: list[str] = []
    headers = _ordered_headers(song) if reorder else list(song.headers)
    for key, value in headers:
        if key == "RELATIVE" or (key == "ENCODING"):
            continue  # we always write absolute beats in UTF-8
        if value == "":
            continue
        out.append(f"#{key}:{value}")

    voices = sorted(v for v, lines in song.voices.items() if lines)
    duet = len(voices) > 1
    for voice in voices:
        if duet:
            out.append(f"P{voice}")
        lines = song.voices[voice]
        for idx, line in enumerate(lines):
            for n in line.notes:
                out.append(f"{n.kind} {n.start} {n.length} {n.pitch} {n.text}")
            if idx < len(lines) - 1:
                beat = line.break_beat if line.break_beat is not None else line.end
                out.append(f"- {beat}")
    out.append("E")
    # Trailer after 'E' (USDX ignores everything after 'E'): which notes the double-check
    # consensus auto-corrected - see the 'auto-fixed' issues in the .ultraai.json sidecar.
    fixed: dict[int, list[int]] = {}
    for v in voices:
        for idx, n in enumerate(song.notes(v)):
            if n.auto_fixed:
                fixed.setdefault(v, []).append(idx)
    for v in sorted(fixed):
        out.append(f"#ULTRAAI-AUTOFIX:{v}:" + ",".join(str(k) for k in fixed[v]))
    return newline.join(out) + newline


def write(song: Song, path: str | Path, **kwargs) -> None:
    Path(path).write_text(dumps(song, **kwargs), encoding="utf-8", newline="")
