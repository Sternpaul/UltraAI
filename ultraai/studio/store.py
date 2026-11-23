"""Studio song library: plain USDX song folders + a small ``.studio`` folder per song.

    LIBRARY/Artist - Title/
        Artist - Title.m4a          audio (as imported)
        Artist - Title.txt          the song (written when processed / saved)
        Artist - Title.ultraai.json issues + sources from the processor
        .studio/song.json           Studio state (id, status, metadata, dismissed issues)
        .studio/editor.json         pitch curve, waveform peaks, transcript (from the processor)
        .studio/vocals.ogg          separated vocals for the editor
        .studio/lyrics.txt          lyrics the user pasted (used instead of LRCLIB)
        .studio/versions/NNN-<what>.txt   every saved version (000 = machine output)

USDX reads the folders directly; the hidden ``.studio`` folder is ignored by the game.
Songs already in the library without ``.studio`` are listed as "imported" and are editable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac", ".webm"}
STATES = ("downloading", "queued", "processing", "needs_lyrics", "needs_review", "reviewed",
          "imported", "error")


def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")[:120] or "Unknown"


@dataclass
class SongState:
    id: str
    artist: str = ""
    title: str = ""
    language: str = ""                  # "" = detect
    state: str = "queued"
    stage: str = ""
    error: str = ""
    source: str = ""                    # url or original file name
    audio: str = ""                     # file name inside the folder
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    priority: int = 0                   # higher first
    job: dict = field(default_factory=dict)   # processor bookkeeping (remote job id, ...)
    issues: int = 0                     # open issues (after dismissals)
    dismissed: list[str] = field(default_factory=list)  # issue keys the user marked OK
    lyrics_source: str = ""
    video: str = ""                     # file name of the music video (USDX #VIDEO), if any
    cover: str = ""                     # cover image (USDX #COVER), if any

    @classmethod
    def from_dict(cls, d: dict) -> "SongState":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index: dict[str, Path] = {}

    # ---- lookup ------------------------------------------------------------
    def scan(self) -> dict[str, Path]:
        """id -> folder.  Folders without Studio state get a stable id from their name."""
        with self._lock:
            idx = {}
            for folder in sorted(p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith(".")):
                st_file = folder / ".studio" / "song.json"
                if st_file.exists():
                    try:
                        sid = json.loads(st_file.read_text(encoding="utf-8"))["id"]
                    except (ValueError, KeyError, OSError):
                        continue
                elif any(folder.glob("*.txt")):
                    sid = "x" + uuid.uuid5(uuid.NAMESPACE_URL, folder.name).hex[:11]
                else:
                    continue
                idx[sid] = folder
            self._index = idx
            return dict(idx)

    def folder(self, sid: str) -> Path:
        with self._lock:
            if sid not in self._index:
                self.scan()
            if sid not in self._index:
                raise KeyError(sid)
            return self._index[sid]

    def state(self, sid: str) -> SongState:
        folder = self.folder(sid)
        f = folder / ".studio" / "song.json"
        if f.exists():
            return SongState.from_dict(json.loads(f.read_text(encoding="utf-8")))
        # a song that was already in the library (made elsewhere)
        txt = self.txt_path(sid)
        artist, title = _split_name(folder.name)
        if txt is not None:
            from ..ultrastar.reader import read

            try:
                song = read(txt)
                artist, title = song.artist or artist, song.title or title
            except Exception:  # noqa: BLE001 - listing must not fail on one bad file
                pass
        audio = next((p.name for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT), "")
        return SongState(sid, artist, title, state="imported", audio=audio, created=folder.stat().st_mtime)

    def save_state(self, st: SongState) -> None:
        with self._lock:
            folder = self.folder(st.id)
            (folder / ".studio").mkdir(exist_ok=True)
            st.updated = time.time()
            tmp = folder / ".studio" / "song.json.tmp"
            tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, folder / ".studio" / "song.json")

    def update(self, sid: str, **kw) -> SongState:
        with self._lock:
            st = self.state(sid)
            for k, v in kw.items():
                setattr(st, k, v)
            self.save_state(st)
            return st

    def all(self) -> list[SongState]:
        out = []
        for sid in self.scan():
            try:
                out.append(self.state(sid))
            except (OSError, ValueError, KeyError):
                continue
        return out

    # ---- files ---------------------------------------------------------------
    def base_name(self, sid: str) -> str:
        st = self.state(sid)
        return safe_name(f"{st.artist} - {st.title}") if st.artist or st.title else self.folder(sid).name

    def txt_path(self, sid: str) -> Path | None:
        folder = self.folder(sid)
        txts = sorted(p for p in folder.glob("*.txt") if not p.name.startswith("."))
        exact = folder / f"{folder.name}.txt"
        return exact if exact.exists() else (txts[0] if txts else None)

    def sidecar_path(self, sid: str) -> Path:
        txt = self.txt_path(sid)
        folder = self.folder(sid)
        return (txt.with_suffix(".ultraai.json") if txt else folder / f"{folder.name}.ultraai.json")

    def studio_dir(self, sid: str) -> Path:
        d = self.folder(sid) / ".studio"
        d.mkdir(exist_ok=True)
        return d

    # ---- create ----------------------------------------------------------------
    def create(self, artist: str, title: str, source: str = "", state: str = "queued",
               language: str = "") -> SongState:
        with self._lock:
            sid = uuid.uuid4().hex[:12]
            name = safe_name(f"{artist} - {title}") if artist or title else f"New song {sid[:4]}"
            folder, k = self.root / name, 2
            while folder.exists():
                folder, k = self.root / f"{name} ({k})", k + 1
            (folder / ".studio").mkdir(parents=True)
            st = SongState(sid, artist, title, language=language, state=state, source=source)
            self._index[sid] = folder
            self.save_state(st)
            return st

    def add_audio(self, sid: str, src: Path, move: bool = False) -> str:
        folder = self.folder(sid)
        name = f"{folder.name}{src.suffix.lower()}"
        dst = folder / name
        (shutil.move if move else shutil.copy2)(str(src), dst)
        self.update(sid, audio=name)
        return name

    def add_media(self, sid: str, src: Path, tag: str, field: str, move: bool = True) -> str:
        """Video / cover next to the song, USDX naming: 'Artist - Title [VD].mp4', '[CO].jpg'."""
        folder = self.folder(sid)
        name = f"{folder.name} [{tag}]{src.suffix.lower()}"
        (shutil.move if move else shutil.copy2)(str(src), folder / name)
        self.update(sid, **{field: name})
        return name

    def apply_media_headers(self, sid: str) -> None:
        """Point the txt at the song's audio, video and cover files."""
        txt = self.txt_path(sid)
        if txt is None:
            return
        from ..ultrastar.reader import read
        from ..ultrastar.writer import write

        st = self.state(sid)
        song = read(txt)
        if st.audio:
            song.set("MP3", st.audio)
            song.set("AUDIO", st.audio)
        if st.video:
            song.set("VIDEO", st.video)
            if not song.has("VIDEOGAP"):
                song.set("VIDEOGAP", "0")  # audio and video come from the same upload
        if st.cover:
            song.set("COVER", st.cover)
        write(song, txt)

    def delete(self, sid: str) -> None:
        with self._lock:
            folder = self.folder(sid)
            trash = self.root / ".trash"
            trash.mkdir(exist_ok=True)
            shutil.move(str(folder), trash / f"{folder.name}-{int(time.time())}")
            self._index.pop(sid, None)

    # ---- versions ------------------------------------------------------------------
    def save_version(self, sid: str, txt_text: str, what: str) -> Path:
        d = self.studio_dir(sid) / "versions"
        d.mkdir(exist_ok=True)
        n = len(list(d.glob("*.txt")))
        p = d / f"{n:03d}-{safe_name(what)}.txt"
        p.write_text(txt_text, encoding="utf-8")
        return p

    def versions(self, sid: str) -> list[dict]:
        d = self.studio_dir(sid) / "versions"
        return [{"name": p.name, "time": p.stat().st_mtime} for p in sorted(d.glob("*.txt"))] if d.exists() else []


def _split_name(name: str) -> tuple[str, str]:
    if " - " in name:
        a, t = name.split(" - ", 1)
        return a.strip(), t.strip()
    return "", name
