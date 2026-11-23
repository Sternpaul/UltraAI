"""UltraAI Studio backend: song library, processing queue and the editor API.

    ultraai studio [--port 8766] [--no-window]

Binds to 127.0.0.1 only.  Mutating requests must carry ``X-UltraAI: 1`` (a browser page
from another site cannot add that header without a CORS preflight, which we never allow).
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..ultrastar.reader import read
from ..ultrastar.validator import validate
from ..ultrastar.writer import dumps
from .config import Settings
from .doc import DocError, doc_to_song, song_to_doc
from .store import Library, SongState

WEB = Path(__file__).with_name("web")


# ---------------------------------------------------------------- processing queue
class Queue:
    """Background threads that take queued songs to a processor, highest priority first."""

    def __init__(self, lib: Library, make_processor, slots: int = 2):
        self.lib, self.make_processor, self.slots = lib, make_processor, slots
        self._pick = threading.Lock()
        self._busy: set[str] = set()
        self._wake = threading.Event()
        self._stop = False
        self.log: list[str] = []

    def start(self):
        for st in self.lib.all():  # resume after a restart
            if st.state == "processing" and not st.job.get("remote_id"):
                self.lib.update(st.id, state="queued", stage="")
        for k in range(self.slots):
            threading.Thread(target=self._loop, daemon=True, name=f"queue-{k}").start()

    def poke(self):
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def _next(self) -> str | None:
        with self._pick:
            songs = [s for s in self.lib.all() if s.id not in self._busy]
            resume = [s for s in songs if s.state == "processing"]
            queued = sorted((s for s in songs if s.state == "queued"), key=lambda s: (-s.priority, s.created))
            nxt = (resume or queued or [None])[0]
            if nxt is not None:
                self._busy.add(nxt.id)
                if nxt.state == "queued":
                    self.lib.update(nxt.id, state="processing", stage="starting", error="")
            return nxt.id if nxt else None

    def _loop(self):
        while not self._stop:
            sid = self._next()
            if sid is None:
                self._wake.wait(10)
                self._wake.clear()
                continue
            try:
                self.make_processor().process(sid, stage=lambda s, sid=sid: self.lib.update(sid, stage=s))
            except Exception as exc:  # noqa: BLE001 - keep the queue alive
                self.log.append(traceback.format_exc()[-2000:])
                try:
                    self.lib.update(sid, state="error", stage="", error=f"{type(exc).__name__}: {exc}", job={})
                except KeyError:
                    pass
            finally:
                self._busy.discard(sid)


# ---------------------------------------------------------------- API models
class UrlIn(BaseModel):
    url: str


class MetaIn(BaseModel):
    artist: str | None = None
    title: str | None = None
    language: str | None = None
    priority: int | None = None


class LyricsIn(BaseModel):
    text: str


class DocIn(BaseModel):
    doc: dict
    note: str = "edit"


class KeyIn(BaseModel):
    key: str


def issue_key(i: dict) -> str:
    return f"{i.get('kind')}:{i.get('start', 0):.2f}:{(i.get('text') or '')[:24]}"


def create_app(settings: Settings, processor_factory=None, start_queue: bool = True) -> FastAPI:
    lib = Library(Path(settings.library))
    if processor_factory is None:
        processor_factory = lambda: make_processor(settings, lib)  # noqa: E731
    queue = Queue(lib, processor_factory, slots=2 if settings.processor == "ssh" else 1)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        if start_queue:
            queue.start()
        yield
        queue.stop()

    app = FastAPI(title="UltraAI Studio", lifespan=lifespan)
    app.state.lib, app.state.queue, app.state.settings = lib, queue, settings

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-ultraai") != "1":
            return JSONResponse({"detail": "missing X-UltraAI header"}, status_code=403)
        return await call_next(request)

    def get_state(sid: str) -> SongState:
        try:
            return lib.state(sid)
        except KeyError:
            raise HTTPException(404, "unknown song") from None

    def card(st: SongState) -> dict:
        d = {k: getattr(st, k) for k in ("id", "artist", "title", "language", "state", "stage", "error",
                                         "source", "created", "updated", "priority", "issues",
                                         "lyrics_source")}
        d["open_issues"] = max(0, st.issues - len(st.dismissed))
        return d

    # ---- library / queue -----------------------------------------------------------
    @app.get("/api/songs")
    def songs():
        order = {s: k for k, s in enumerate(("processing", "downloading", "needs_lyrics", "needs_review",
                                              "queued", "error", "reviewed", "imported"))}
        items = [card(s) for s in lib.all()]
        items.sort(key=lambda c: (order.get(c["state"], 9), -c["open_issues"], -c["updated"]))
        return items

    @app.post("/api/songs/url")
    def add_url(body: UrlIn):
        from .importer import import_youtube

        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "not a link")
        holder: dict = {}

        def work():
            try:
                st = import_youtube(lib, url, progress=lambda m: holder.get("id") and lib.update(holder["id"], stage=m))
                holder["id"] = st.id
                queue.poke()
            except Exception:  # noqa: BLE001 - state 'error' is set on the song
                pass

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    @app.post("/api/songs/files")
    async def add_files(files: list[UploadFile] = File(...)):
        import tempfile

        from .importer import import_file

        out = []
        for f in files:
            suffix = Path(f.filename or "audio").suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                while chunk := await f.read(1 << 20):
                    tmp.write(chunk)
            st = import_file(lib, Path(tmp.name), original_name=f.filename, move=True)
            out.append(card(st))
        queue.poke()
        return out

    @app.get("/api/songs/{sid}")
    def song(sid: str):
        st = get_state(sid)
        d = card(st)
        sd = lib.folder(sid) / ".studio"
        draft = sd / "lyrics_draft.txt"
        lyr = sd / "lyrics.txt"
        d.update(versions=lib.versions(sid), has_txt=lib.txt_path(sid) is not None,
                 lyrics=lyr.read_text(encoding="utf-8") if lyr.exists() else "",
                 lyrics_draft=draft.read_text(encoding="utf-8") if draft.exists() else "")
        return d

    @app.patch("/api/songs/{sid}")
    def edit_meta(sid: str, body: MetaIn):
        get_state(sid)
        kw = {k: v for k, v in body.model_dump().items() if v is not None}
        return card(lib.update(sid, **kw))

    @app.post("/api/songs/{sid}/lyrics")
    def set_lyrics(sid: str, body: LyricsIn):
        st = get_state(sid)
        (lib.studio_dir(sid) / "lyrics.txt").write_text(body.text.strip() + "\n", encoding="utf-8")
        lib.update(sid, state="queued", stage="", error="", priority=max(st.priority, 1), job={})
        queue.poke()
        return {"ok": True}

    @app.post("/api/songs/{sid}/process")
    def reprocess(sid: str):
        st = get_state(sid)
        if st.state in ("processing", "downloading"):
            raise HTTPException(409, "already running")
        if not st.audio:
            raise HTTPException(400, "song has no audio")
        lib.update(sid, state="queued", stage="", error="", job={})
        queue.poke()
        return {"ok": True}

    @app.delete("/api/songs/{sid}")
    def delete(sid: str):
        get_state(sid)
        lib.delete(sid)
        return {"ok": True}

    # ---- editor ------------------------------------------------------------------
    @app.get("/api/songs/{sid}/doc")
    def get_doc(sid: str):
        st = get_state(sid)
        txt = lib.txt_path(sid)
        if txt is None:
            raise HTTPException(404, "song has not been processed yet")
        song = read(txt)
        side = {}
        sp = txt.with_suffix(".ultraai.json")
        if sp.exists():
            side = json.loads(sp.read_text(encoding="utf-8"))
        issues = []
        for i in side.get("issues", []):
            i = dict(i, key=issue_key(i))
            i["dismissed"] = i["key"] in st.dismissed
            issues.append(i)
        sd = lib.folder(sid) / ".studio"
        base = f"/api/songs/{sid}/media"
        return {"doc": song_to_doc(song), "issues": issues, "state": st.state,
                "audio": f"{base}/audio" if st.audio or song.audio else None,
                "vocals": f"{base}/vocals" if (sd / "vocals.ogg").exists() else None,
                "editor": f"{base}/editor" if (sd / "editor.json").exists() else None,
                "lyrics_source": side.get("lyrics_source", st.lyrics_source)}

    @app.put("/api/songs/{sid}/doc")
    def put_doc(sid: str, body: DocIn):
        get_state(sid)
        txt = lib.txt_path(sid)
        if txt is None:
            raise HTTPException(404, "song has not been processed yet")
        try:
            song = doc_to_song(body.doc)
        except (DocError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"invalid song: {exc}") from None
        text = dumps(song)
        lib.save_version(sid, text, body.note or "edit")
        tmp = txt.with_suffix(".txt.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(txt)
        problems = [str(i) for i in validate(read(txt), txt.parent) if i.level in ("error", "warning")]
        return {"ok": True, "problems": problems, "saved": time.time()}

    @app.post("/api/songs/{sid}/issues/dismiss")
    def dismiss(sid: str, body: KeyIn):
        st = get_state(sid)
        if body.key not in st.dismissed:
            lib.update(sid, dismissed=st.dismissed + [body.key])
        return {"ok": True}

    @app.post("/api/songs/{sid}/reviewed")
    def reviewed(sid: str):
        get_state(sid)
        return card(lib.update(sid, state="reviewed", stage=""))

    @app.get("/api/songs/{sid}/media/{what}")
    def media(sid: str, what: str):
        st = get_state(sid)
        folder = lib.folder(sid)
        if what == "audio":
            name = st.audio
            if not name and (txt := lib.txt_path(sid)):
                name = read(txt).audio or ""
            path = folder / name if name else None
        elif what == "vocals":
            path = folder / ".studio" / "vocals.ogg"
        elif what == "editor":
            path = folder / ".studio" / "editor.json"
        else:
            raise HTTPException(404, "unknown media")
        if path is None or not path.is_file() or path.resolve().parent not in (folder.resolve(),
                                                                                  (folder / ".studio").resolve()):
            raise HTTPException(404, "not found")
        ctype = "audio/mp4" if path.suffix.lower() == ".m4a" else None
        return FileResponse(path, media_type=ctype)

    # ---- settings -----------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        from dataclasses import asdict

        return asdict(settings)

    @app.put("/api/settings")
    def put_settings(body: dict):
        for k, v in body.items():
            if k in Settings.__dataclass_fields__:
                setattr(settings, k, v)
        settings.save()
        return {"ok": True, "restart_needed": "library" in body}

    @app.post("/api/settings/test")
    def test_connection():
        p = make_processor(settings, lib)
        try:
            return {"ok": True, "result": p.check() if hasattr(p, "check") else "local"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @app.get("/api/log")
    def log():
        return queue.log[-20:]

    if WEB.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def make_processor(settings: Settings, lib: Library):
    from .processors import LocalProcessor, SshProcessor, SshTransport

    sep = settings.extra.get("separation")
    if settings.processor == "local":
        return LocalProcessor(lib, separation=sep)
    if not settings.ssh_host:
        raise RuntimeError("no server configured (Settings: SSH host)")
    return SshProcessor(lib, SshTransport(settings.ssh_host, settings.ssh_port or None),
                        settings.remote_dir, settings.remote_ultraai, separation=sep)


def run(port: int = 8766, open_window: bool = True) -> None:
    import uvicorn

    settings = Settings.load()
    app = create_app(settings)
    url = f"http://127.0.0.1:{port}/"
    if not open_window:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        return
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        import webview  # pywebview: its own window (Edge WebView2 on Windows)

        webview.create_window("UltraAI Studio", url, width=1400, height=900)
        webview.start()
    except ImportError:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
