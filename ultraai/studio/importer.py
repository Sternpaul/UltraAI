"""Getting songs in: audio files (with tags or "Artist - Title" names) and YouTube links."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .store import Library, SongState

_NOISE = re.compile(r"\s*[(\[](?:official|lyric|lyrics|audio|video|music video|hd|hq|4k|visualizer|"
                    r"remaster(?:ed)?(?: \d{4})?|explicit|clip officiel|videoclip)[^)\]]*[)\]]", re.I)


def guess_artist_title(text: str) -> tuple[str, str]:
    """'Artist - Title (Official Video)' -> ('Artist', 'Title')."""
    text = _NOISE.sub("", text).strip()
    text = re.sub(r"\s*\|.*$", "", text)  # "Title | Channel"
    for sep in (" - ", " – ", " — ", " -- ", ": "):
        if sep in text:
            a, t = text.split(sep, 1)
            return a.strip(), t.strip(" \"'")
    return "", text.strip()


def tags_of(path: Path) -> tuple[str, str]:
    try:
        import mutagen

        f = mutagen.File(path, easy=True)
        if f is not None and f.tags:
            artist = (f.tags.get("artist") or [""])[0]
            title = (f.tags.get("title") or [""])[0]
            if artist and title:
                return artist, title
    except Exception:  # noqa: BLE001 - tags are optional
        pass
    return guess_artist_title(path.stem)


def import_file(lib: Library, path: Path, original_name: str | None = None, move: bool = False) -> SongState:
    artist, title = tags_of(path)
    if not artist and original_name:
        artist, title = guess_artist_title(Path(original_name).stem)
    st = lib.create(artist, title, source=original_name or path.name)
    lib.add_audio(st.id, path, move=move)
    return lib.state(st.id)


def youtube_info(url: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True}) as y:
        return y.extract_info(url, download=False)


def import_youtube(lib: Library, url: str, progress=None) -> SongState:
    """Create the song right away (state 'downloading'), then download its audio."""
    import yt_dlp

    st = lib.create("", url, source=url, state="downloading")
    try:
        info = youtube_info(url)
        artist = info.get("artist") or info.get("creator") or ""
        title = info.get("track") or ""
        if not (artist and title):
            artist, title = guess_artist_title(info.get("title", ""))
            artist = artist or info.get("uploader", "").removesuffix(" - Topic")
        lib.update(st.id, artist=artist, title=title or info.get("title", url))

        def hook(d):
            if progress and d.get("status") == "downloading" and d.get("total_bytes"):
                progress(f"downloading {100 * d['downloaded_bytes'] / d['total_bytes']:.0f} %")

        with tempfile.TemporaryDirectory() as tmp:
            opts = {"quiet": True, "noplaylist": True, "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": str(Path(tmp) / "audio.%(ext)s"), "progress_hooks": [hook]}
            with yt_dlp.YoutubeDL(opts) as y:
                y.download([url])
            got = next(Path(tmp).iterdir())
            lib.add_audio(st.id, got, move=True)
        return lib.update(st.id, state="queued", stage="")
    except Exception as exc:  # noqa: BLE001 - shown on the song card
        lib.update(st.id, state="error", error=f"download failed: {exc}")
        raise
