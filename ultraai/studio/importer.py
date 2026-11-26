"""Getting songs in: audio files (with tags or "Artist - Title" names) and YouTube links."""

from __future__ import annotations

import importlib.util
import re
import tempfile
from pathlib import Path

import logging

from .store import Library, SongState, save_user_lyrics, youtube_id

log = logging.getLogger("ultraai.import")

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


_impersonate_warned = False


def _cookies_from(settings) -> str | None:
    """The cookies path from the Studio settings (dialog), or None -> env fallback.

    A settings value that is unset, empty or missing falls back to ULTRAAI_COOKIES.
    """
    return (str(getattr(settings, "cookies", "") or "")).strip() or None


def _impersonation_available(target: str) -> bool:
    """Whether yt-dlp can really impersonate: curl_cffi is what it does TLS with.

    curl_cffi is a [worker] extra, so the base (Studio) install may not have it;
    passing ``impersonate`` to YoutubeDL then fails for every YouTube request.
    Same rule as ytsubs._impersonation_available() (duplicated here: importing
    ytsubs would pull in numpy via audio.py).  Warns once per process when
    impersonation was requested but is not possible."""
    global _impersonate_warned
    if importlib.util.find_spec("curl_cffi") is not None:
        return True
    if not _impersonate_warned:
        _impersonate_warned = True
        log.warning("ULTRAAI_YT_IMPERSONATE=%s is set but curl_cffi is not installed; "
                    "YouTube requests run without browser impersonation "
                    "(pip install curl_cffi)", target)
    return False


def _cookie_opts(cookies: str | None = None) -> dict:
    """yt-dlp options: ``cookiefile`` for an explicit cookies.txt path or, when
    ``None``, ULTRAAI_COOKIES — each only when it names an existing file — and
    ``impersonate`` from ULTRAAI_YT_IMPERSONATE (lifts most YouTube 429s; only when
    curl_cffi is installed).

    Same rule as ytsubs._ytdlp_extra() (duplicated here: importing ytsubs would
    pull in numpy via audio.py).  Only the path is passed on — the file is a secret.
    """
    from .. import config

    opts: dict = {}
    path = (cookies if cookies is not None else config.get("cookies")).strip()
    if path and Path(path).is_file():
        opts["cookiefile"] = path
    target = config.get("yt_impersonate").strip()
    if target and _impersonation_available(target):
        opts["impersonate"] = target  # dropped when curl_cffi is missing (yt_dlp default is None)
    return opts


def youtube_info(url: str, cookies: str | None = None) -> dict:
    import yt_dlp

    from .logs import YtdlpLogger

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True,
                           "logger": YtdlpLogger(), **_cookie_opts(cookies)}) as y:
        return y.extract_info(url, download=False)


AUDIO_FORMATS = {
    "m4a": "bestaudio[ext=m4a]/bestaudio/best",     # AAC ~128 kbit/s: every player
    "best": "bestaudio/best",                       # usually Opus ~130-160 kbit/s in WebM
}


def video_format(quality: str) -> str | None:
    """Video only (audio is separate); H.264 first, it plays in every USDX build."""
    if quality in ("none", "", None):
        return None
    cap = "" if quality == "best" else f"[height<={int(quality)}]"
    return f"bestvideo[vcodec^=avc1]{cap}/bestvideo[ext=mp4]{cap}/bestvideo{cap}"


def _download(url: str, fmt: str, dest: Path, hook=None, cookies: str | None = None) -> Path:
    import yt_dlp

    from .logs import YtdlpLogger

    opts = {"quiet": True, "noplaylist": True, "format": fmt, "outtmpl": str(dest / "dl.%(ext)s"),
            "progress_hooks": [hook] if hook else [], "logger": YtdlpLogger(), "verbose": True,
            **_cookie_opts(cookies)}
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    return next(p for p in dest.iterdir() if p.name.startswith("dl."))


def ffmpeg_path() -> str | None:
    import shutil

    return shutil.which("ffmpeg")


def remux_audio(src: Path) -> Path:
    """WebM/Opus -> Ogg/Opus: same audio stream, no re-encoding (lossless, ~1 s), in a
    container every USDX build plays.  Unchanged if ffmpeg is missing or it fails."""
    import subprocess

    if src.suffix.lower() not in (".webm", ".mka", ".mkv"):
        return src
    ff = ffmpeg_path()
    if not ff:
        log.warning("ffmpeg not found: keeping %s", src.name)
        return src
    dst = src.with_suffix(".ogg")
    r = subprocess.run([ff, "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-c:a", "copy", str(dst)],
                       capture_output=True, text=True, creationflags=0x08000000 if __import__("os").name == "nt" else 0)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        log.warning("remux of %s failed: %s", src.name, r.stderr.strip()[-300:])
        return src
    log.debug("remuxed %s -> %s", src.name, dst.name)
    src.unlink(missing_ok=True)
    return dst


def _cover_url(info: dict) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if str(t.get("url", "")).split("?")[0].endswith(".jpg")]
    if not thumbs:
        return info.get("thumbnail")
    return max(thumbs, key=lambda t: (t.get("preference") or 0, t.get("width") or 0))["url"]


def fetch_extras(lib: Library, sid: str, settings, info: dict | None = None, progress=None) -> list[str]:
    """Music video and cover for a song that came from a link (also for songs added earlier)."""
    import urllib.request

    st = lib.state(sid)
    if not st.source.startswith(("http://", "https://")):
        return []
    info = info or youtube_info(st.source, _cookies_from(settings))
    got = []
    fmt = video_format(getattr(settings, "youtube_video", "none"))
    if fmt and not st.video:
        progress and progress("downloading video")
        with tempfile.TemporaryDirectory() as tmp:
            f = _download(st.source, fmt, Path(tmp), _hook(progress, "video"), _cookies_from(settings))
            got.append(lib.add_media(sid, f, "VD", "video"))
    if getattr(settings, "youtube_cover", False) and not st.cover and (cu := _cover_url(info)):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "cover.jpg"
            req = urllib.request.Request(cu, headers={"User-Agent": "Mozilla/5.0"})
            f.write_bytes(urllib.request.urlopen(req, timeout=30).read())
            got.append(lib.add_media(sid, f, "CO", "cover"))
    lib.apply_media_headers(sid)
    return got


def _hook(progress, what: str):
    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(f"downloading {what} {100 * d['downloaded_bytes'] / total:.0f} %")
    return hook


def import_youtube(lib: Library, url: str, progress=None, settings=None, options: dict | None = None,
                   lyrics: str = "") -> SongState:
    """Create the song right away (state 'downloading'), then download audio, video, cover.

    ``lyrics`` (pasted at add time) are stored in .studio/lyrics.txt right after the song
    exists — while still 'downloading', so the queue cannot pick the song up without them."""
    st = lib.create("", url, source=url, state="downloading")
    if lyrics.strip():
        save_user_lyrics(lib, st.id, lyrics)
    if options:
        lib.update(st.id, options=options)
    ck = _cookies_from(settings)
    try:
        info = youtube_info(url, ck)
        artist = info.get("artist") or info.get("creator") or ""
        title = info.get("track") or ""
        if not (artist and title):
            artist, title = guess_artist_title(info.get("title", ""))
            artist = artist or info.get("uploader", "").removesuffix(" - Topic")
        lib.update(st.id, artist=artist, title=title or info.get("title", url))
        lib.rename_to_meta(st.id)  # "Artist - Title" folder before anything is saved in it

        fmt = AUDIO_FORMATS.get(getattr(settings, "youtube_audio", "m4a"), AUDIO_FORMATS["m4a"])
        with tempfile.TemporaryDirectory() as tmp:
            got = _download(url, fmt, Path(tmp), _hook(progress, "audio"), ck)
            got = remux_audio(got)
            audio_name = lib.add_audio(st.id, got, move=True)
            ytid = youtube_id(url) or str(info.get("id") or "")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", ytid):  # <audio-stem>.ytid next to the audio
                (lib.folder(st.id) / (Path(audio_name).stem + ".ytid")).write_text(
                    ytid + "\n", encoding="utf-8")
            log.info("downloaded %s - %s: %s (video id %s)", artist, title, got.suffix, ytid or "?")
        # the song can be processed while the video downloads
        lib.update(st.id, state="queued", stage="")
        if settings is not None:
            try:
                fetch_extras(lib, st.id, settings, info, progress)
            except Exception as exc:  # noqa: BLE001 - audio is what matters
                lib.update(st.id, error=f"video/cover download failed: {exc}")
        return lib.state(st.id)
    except Exception as exc:  # noqa: BLE001 - shown on the song card
        lib.update(st.id, state="error", error=f"download failed: {exc}")
        raise
