"""Song by-products written next to the .txt: a word-synced .lrc and a karaoke .ass.

Both are derived from the Song alone (BPM/GAP turn beats into seconds), so they can be
regenerated for any existing .txt.  ``write_byproducts`` is called wherever a song .txt
is written; each file is only written when its config option is on
(ULTRAAI_EXPORT_LRC / ULTRAAI_EXPORT_ASS).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config
from .ultrastar.model import Song, display_text


def _cs(seconds: float) -> int:
    """Centiseconds, rounded — the precision both LRC and ASS timestamps use."""
    return int(round(seconds * 100))


def _lrc_tag(seconds: float) -> str:
    """``[mm:ss.xx]`` at centisecond precision (round-trips through the same rounding)."""
    m, rem = divmod(_cs(seconds), 6000)
    s, c = divmod(rem, 100)
    return f"[{m:02d}:{s:02d}.{c:02d}]"


def _ass_time(seconds: float) -> str:
    """``h:mm:ss.xx`` (ASS timestamps are centiseconds too)."""
    h, rem = divmod(_cs(seconds), 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def lrc_text(song: Song) -> str:
    """Word-synced LRC: one ``[mm:ss.xx]`` tag per note at the note's start time, the note
    text after it; the tags of one screen line are grouped on a single LRC line."""
    out: list[str] = []
    for line in song.lines():
        parts = [f"{_lrc_tag(song.beat_to_seconds(n.start))}{n.text}" for n in line.notes]
        if parts:
            out.append("".join(parts))
    return "\n".join(out) + ("\n" if out else "")


def ass_text(song: Song) -> str:
    """Minimal karaoke .ass: one Dialogue line per song line, every note a ``\\kf`` span
    sized by its duration in centiseconds (beats via the song's BPM/GAP)."""
    out = [
        "[Script Info]",
        f"Title: {song.title}",
        f"Artist: {song.artist}",
        "ScriptType: v4.00+",
        "PlayResX: 384",
        "PlayResY: 288",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for line in song.lines():
        if not line.notes:
            continue
        start = song.beat_to_seconds(line.start)
        end = song.beat_to_seconds(line.end)
        spans = []
        for n in line.notes:
            dur = _cs(song.beat_to_seconds(n.end) - song.beat_to_seconds(n.start))
            spans.append(f"{{\\kf{max(dur, 1)}}}{display_text(n)}")
        out.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,," + "".join(spans))
    return "\n".join(out) + "\n"


def write_byproducts(song: Song, txt_path: str | Path) -> list[Path]:
    """Write the enabled sidecars (``<stem>.lrc`` / ``<stem>.ass``) next to the .txt.

    Each file is written atomically (tmp + replace).  Returns the paths written.
    """
    txt = Path(txt_path)
    jobs: list[tuple[Path, str]] = []
    if config.get_bool("export_lrc"):
        jobs.append((txt.with_suffix(".lrc"), lrc_text(song)))
    if config.get_bool("export_ass"):
        jobs.append((txt.with_suffix(".ass"), ass_text(song)))
    written: list[Path] = []
    for path, text in jobs:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, path)
        written.append(path)
    return written
