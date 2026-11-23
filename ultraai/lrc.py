"""LRCLIB lyrics: fetch (cached), parse synced LRC, pick the version matching our audio."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .audio import CACHE_DIR

API = "https://lrclib.net/api/search?"
USER_AGENT = "UltraAI/0.1 (karaoke file generator)"
_TIME = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


@dataclass
class LrcLine:
    time: float   # seconds
    text: str


@dataclass
class LrcResult:
    artist: str
    title: str
    duration: float
    lines: list[LrcLine]          # synced lines (empty if only plain lyrics)
    plain: list[str]

    @property
    def synced(self) -> bool:
        return bool(self.lines)

    @property
    def texts(self) -> list[str]:
        return [ln.text for ln in self.lines] if self.lines else self.plain


def parse_lrc(text: str) -> list[LrcLine]:
    out: list[LrcLine] = []
    for raw in text.splitlines():
        stamps = list(_TIME.finditer(raw))
        if not stamps:
            continue
        lyric = _TIME.sub("", raw).strip()
        for m in stamps:
            t = int(m.group(1)) * 60 + float(m.group(2).replace(":", "."))
            out.append(LrcLine(t, lyric))
    out.sort(key=lambda ln: ln.time)
    return [ln for ln in out if ln.text and not re.fullmatch(r"[♪♫\s]*", ln.text)]


def audio_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def _clean_title(t: str) -> str:
    return re.sub(r"\s*[(\[](feat|ft|album|live|greek|esc|radio|remaster)[^)\]]*[)\]]", "", t, flags=re.I).strip()


def _query(params: dict) -> list[dict]:
    """One LRCLIB search, cached on disk."""
    cache_dir = CACHE_DIR / "lrclib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if set(params) == {"artist_name", "track_name"}:  # old cache naming
        name = f"{params['artist_name']} - {params['track_name']}"
    else:
        name = "q_" + "_".join(f"{k}={v}" for k, v in sorted(params.items()))
    cache = cache_dir / (re.sub(r"[^\w]+", "_", name)[:180] + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    req = urllib.request.Request(API + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            res = json.load(urllib.request.urlopen(req, timeout=20))
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)
    cache.write_text(json.dumps(res))
    return res


def _sim(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    norm = lambda x: re.sub(r"[^\w]+", " ", x.lower()).strip()  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def search(artist: str, title: str, duration: float | None = None) -> list[dict]:
    """LRCLIB results for a song, trying looser queries until something is found:
    full artist; first credited artist; title without any bracketed suffix; title alone
    (then only results whose artist or duration fits)."""
    artist = _clean_title(artist).replace("\u2019", "'")
    title = _clean_title(title).replace("\u2019", "'")
    first = re.split(r"\s+(?:feat\.?|ft\.?|&|x|vs\.?|with|und)\s+|\s*[,/]\s*", artist, flags=re.I)[0]
    bare = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", title).strip() or title
    legacy = re.split(r"\s+(feat\.?|ft\.?|&)\s+", artist, flags=re.I)[0]  # the original query
    tries = [(legacy, title), (artist, title), (first, title), (artist, bare), (first, bare)]
    seen = set()
    for a, t in tries:
        if (a, t) in seen or not a or not t:
            continue
        seen.add((a, t))
        res = _query({"artist_name": a, "track_name": t})
        if any(r.get("syncedLyrics") or r.get("plainLyrics") for r in res):
            return res
    # title only: keep results by a similar artist, or (same title) a matching duration
    res = _query({"track_name": bare})
    keep = [r for r in res if _sim(r.get("trackName", ""), bare) > 0.8 and (
        _sim(r.get("artistName", ""), artist) > 0.6 or _sim(r.get("artistName", ""), first) > 0.6
        or first.lower() in r.get("artistName", "").lower()
        or (duration and abs((r.get("duration") or 0) - duration) <= 2.0))]
    return keep


def best_match(results: list[dict], duration: float | None) -> LrcResult | None:
    """Prefer synced lyrics with the closest duration; fall back to plain lyrics."""
    synced = [r for r in results if r.get("syncedLyrics")]
    pool = synced or [r for r in results if r.get("plainLyrics")]
    if not pool:
        return None
    if duration:
        pool.sort(key=lambda r: abs((r.get("duration") or 0) - duration))
    r = pool[0]
    return LrcResult(r.get("artistName", ""), r.get("trackName", ""), float(r.get("duration") or 0),
                     parse_lrc(r.get("syncedLyrics") or ""),
                     [ln.strip() for ln in (r.get("plainLyrics") or "").splitlines() if ln.strip()])


def _to_result(r: dict) -> LrcResult:
    return LrcResult(r.get("artistName", ""), r.get("trackName", ""), float(r.get("duration") or 0),
                     parse_lrc(r.get("syncedLyrics") or ""),
                     [ln.strip() for ln in (r.get("plainLyrics") or "").splitlines() if ln.strip()])


def candidates(results: list[dict], duration: float | None, limit: int = 6) -> list[LrcResult]:
    """Distinct lyric versions, closest duration first (synced preferred on ties)."""
    pool = [r for r in results if r.get("syncedLyrics") or r.get("plainLyrics")]
    pool.sort(key=lambda r: (abs((r.get("duration") or 0) - duration) if duration else 0,
                             not r.get("syncedLyrics")))
    out, seen = [], set()
    for r in pool:
        res = _to_result(r)
        key = "\n".join(t.strip().lower() for t in res.texts)
        if key in seen:
            continue
        seen.add(key)
        out.append(res)
        if len(out) >= limit:
            break
    return out


def fetch_candidates(artist: str, title: str, audio: Path | None = None, limit: int = 6) -> list[LrcResult]:
    dur = audio_duration(audio) if audio else None
    return candidates(search(artist, title, dur), dur, limit)


def fetch(artist: str, title: str, audio: Path | None = None) -> LrcResult | None:
    dur = audio_duration(audio) if audio else None
    return best_match(search(artist, title, dur), dur)
