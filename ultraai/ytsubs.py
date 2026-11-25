"""YouTube subtitles (manual preferred over auto) as a timed-lyrics source, cached.

``fetch_subs`` runs yt-dlp once per video (json3 subtitle format) and caches the raw
json3 dict at ``CACHE_DIR/yt/<ytid>.json`` (atomic tmp+replace, like lrc.py's cache
layout).  ``sub_segments``/``sub_lines`` parse the cached json3 into word/line views,
and ``word_agreement`` scores how well a Whisper transcript matches the subtitles.
``resolve_ytid`` maps an "ARTIST - TITLE" query to a video id (ytsearch1), cached too.

All public functions never raise: any failure (no yt-dlp, no network, no subs,
corrupt cache) yields ``None`` / ``[]`` / ``0.0``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .audio import CACHE_DIR
from .lyrics import clean_lines

YT_URL = "https://www.youtube.com/watch?v={ytid}"

# --sub-langs value: manual languages first; auto captions only fill languages
# without a manual track (that per-language preference is yt-dlp's own).
SUB_LANGS = ["en", "en-US", "en-GB", "el"]

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW, as in studio/importer.py


@dataclass
class SubWord:
    start: float   # seconds
    end: float     # seconds
    text: str


@dataclass
class SubSegment:
    start: float          # seconds (event tStartMs)
    end: float            # seconds (event tStartMs + dDurationMs)
    text: str             # words joined with single spaces
    words: list[SubWord]


def _run(cmd: list[str]) -> str:
    """Run a command and return its stdout; raises RuntimeError on non-zero exit.

    The single subprocess choke point: tests monkeypatch this, no real network.
    """
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed (rc={r.returncode}): {r.stderr.strip()[-300:]}")
    return r.stdout


def _cache_write(cache: Path, data) -> None:
    """Atomic cache write: tmp file in the same dir, then replace (lrc.py's layout style)."""
    tmp = cache.with_name(cache.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(cache)


def _pick_sub_file(tmp: Path) -> Path | None:
    """Choose the json3 file to keep: manual (unmarked) over auto ('.auto.' in the
    name), then the language that comes first in SUB_LANGS."""
    files = sorted(tmp.glob("*.json3"))
    if not files:
        return None

    def key(f: Path):
        lang = f.name[len("sub."):-len(".json3")] if f.name.startswith("sub.") else f.stem
        try:
            order = SUB_LANGS.index(lang)
        except ValueError:
            order = len(SUB_LANGS)
        return (".auto." in f.name, order, f.name)

    return sorted(files, key=key)[0]


def fetch_subs(ytid: str, manual: bool = True) -> dict | None:
    """Raw json3 subtitle dict ({"events": [...]}) for a YouTube video, cached at
    CACHE_DIR/yt/<ytid>.json.  Runs yt-dlp with --write-subs (when ``manual``) and
    --write-auto-subs, --sub-format json3, --sub-langs <SUB_LANGS>; yt-dlp itself
    prefers a manual track over the auto caption of the same language.  Returns
    None on any failure (never raises)."""
    try:
        cache_dir = CACHE_DIR / "yt"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / f"{ytid}.json"
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                pass  # corrupt cache: refetch
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["yt-dlp", "--skip-download", "--sub-format", "json3",
                   "--sub-langs", ",".join(SUB_LANGS)]
            if manual:
                cmd.append("--write-subs")
            cmd += ["--write-auto-subs", "-o", str(Path(tmp) / "sub.%(ext)s"),
                    YT_URL.format(ytid=ytid)]
            _run(cmd)
            sub = _pick_sub_file(Path(tmp))
            if sub is None:
                return None
            data = json.loads(sub.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("events"), list):
            return None
        _cache_write(cache, data)
        return data
    except Exception:
        return None


def _is_music_event(text: str) -> bool:
    """A pure '[Music]'-style event: after removing bracketed tags, no word chars left."""
    stripped = re.sub(r"[\[\(][^\]\)]*[\]\)]", "", text)
    return not re.search(r"\w", stripped)


def sub_segments(ytid: str) -> list[SubSegment]:
    """Parsed segments from the cached json3: one per event with segs, times in
    seconds (json3 ms / 1000).  Pure '[Music]'-style events and empty words dropped."""
    data = fetch_subs(ytid)
    if not data:
        return []
    out: list[SubSegment] = []
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        base_ms = ev.get("tStartMs") or 0
        dur_ms = ev.get("dDurationMs") or 0
        words: list[SubWord] = []
        offset = 0
        for seg in ev.get("segs") or []:
            text = (seg.get("utf8") or "").strip()
            seg_dur = seg.get("dDurationMs", seg.get("durationMs")) or 0
            if not text:
                offset += seg_dur
                continue
            t = seg.get("tStartMs")
            start = (t if t is not None else base_ms + offset) / 1000.0
            offset += seg_dur
            words.append(SubWord(start, start + seg_dur / 1000.0, text))
        text = " ".join(w.text for w in words)
        if not words or _is_music_event(text):
            continue
        out.append(SubSegment(base_ms / 1000.0, (base_ms + dur_ms) / 1000.0, text, words))
    return out


def sub_lines(ytid: str) -> tuple[list[str], list[float]]:
    """(line texts, line start seconds).  Line text = the segment's words joined,
    cleaned with lyrics.clean_lines (applied per line so times stay aligned);
    line start = first word start of the segment."""
    lines: list[str] = []
    times: list[float] = []
    for seg in sub_segments(ytid):
        if not seg.words:
            continue
        text = " ".join(w.text for w in seg.words)
        for cleaned in clean_lines([text]):
            lines.append(cleaned)
            times.append(seg.words[0].start)
    return lines, times


def _word_text(w) -> str:
    """AsrWord has .text; plain (start, end, text) tuples use index 2."""
    return w.text if hasattr(w, "text") else w[2]


def _tokens(text: str) -> list[str]:
    """Normalized tokens: lowercase, punctuation stripped."""
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def word_agreement(asr_words: list, sub_words: list[SubWord]) -> float:
    """Normalized-token agreement between two transcripts, 0..1 (pure function, no I/O).

    Tokens are normalized (lowercase, punctuation stripped).  With A = the asr token
    multiset and B = the sub token multiset, agreement = |A ∩ B| / |A ∪ B| (multiset
    intersection over union, i.e. sum of per-token min over sum of per-token max).
    1.0 = identical token multisets, 0.0 = disjoint.  ``asr_words`` may be AsrWord
    objects or (start, end, text) tuples.
    """
    a = Counter(t for w in asr_words for t in _tokens(_word_text(w)))
    b = Counter(t for w in sub_words for t in _tokens(w.text))
    union = sum((a | b).values())
    if not union:
        return 0.0
    return sum((a & b).values()) / union


def resolve_ytid(query: str) -> str | None:
    """Resolve an 'ARTIST - TITLE' query to a YouTube video id via yt-dlp
    'ytsearch1:<query>' --skip-download --print id; cached under CACHE_DIR/yt/.
    Returns the id or None; never raises."""
    try:
        cache_dir = CACHE_DIR / "yt"
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w]+", "_", query.strip())[:120].strip("_") or "empty"
        cache = cache_dir / f"search_{name}.txt"
        if cache.exists():
            return cache.read_text(encoding="utf-8").strip() or None
        out = _run(["yt-dlp", "--skip-download", "--print", "id", f"ytsearch1:{query}"])
        ytid = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not ytid:
            return None
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(ytid, encoding="utf-8")
        tmp.replace(cache)
        return ytid
    except Exception:
        return None
