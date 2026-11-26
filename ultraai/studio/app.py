"""UltraAI Studio backend: song library, processing queue and the editor API.

    ultraai studio [--port 8766] [--no-window]

Binds to 127.0.0.1 only.  Every request that is not plain GET must carry
``x-ultraai: <token>`` — the per-install token from ``token.txt`` in the config dir
(injected into index.html as a meta tag) — compared with hmac.compare_digest, so a
browser page from another site cannot add that header without a CORS preflight, which
we never allow.  The SSE stream ``GET /api/events`` takes the same token in the header
too (the web client consumes it with fetch streaming, not EventSource), so the token
never appears in a URL or query string: with ``trace`` logging, request lines (including
query strings) end up in the log ring shown in the app, so query strings must stay
secret-free.  Every request must also carry a matching ``Host`` header
(DNS-rebinding protection).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..ultrastar.reader import read
from ..ultrastar.validator import validate
from ..ultrastar.writer import dumps
from .config import Settings, atomic_write, load_token
from .doc import DocError, doc_to_song, song_to_doc
from .logs import TRACE, LEVELS, ring
from .processors import Cancelled
from .store import Library, SongState, VersionConflict

log = logging.getLogger("ultraai.studio")


WEB = Path(__file__).with_name("web")

MAX_UPLOAD = 2 * 1024 ** 3  # 2 GiB per POST /api/songs/files
MAX_EVENT_CLIENTS = 20      # concurrent GET /api/events streams
EVENT_QUEUE_MAX = 100       # events buffered per SSE client; drop-oldest on overflow
EVENT_TICK = 1.0            # s: the event watcher polls the library rev + log cursor this often
EVENT_PING = 15.0           # s without any event before the stream sends a ": ping" comment


def _sse(event: str, data, event_id: int | None = None) -> str:
    """One Server-Sent-Events frame (the JSON payload is a single data: line)."""
    out = f"id: {event_id}\n" if event_id is not None else ""
    return out + f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class EventBus:
    """Fan-out behind GET /api/events: one asyncio task polls the library version counter
    (Library.rev) and the log ring once per second and pushes the full /api/songs payload
    / the new log entries onto a per-client asyncio.Queue.  All blocking store/ring work
    runs in a worker thread, so the event loop is never blocked."""

    def __init__(self, lib: Library, songs_payload):
        self.lib, self.songs_payload = lib, songs_payload
        self.clients: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._last_rev = 0
        self._last_log_id = 0

    def start(self):
        self._last_rev = self.lib.rev
        self._last_log_id = ring.last_id
        self._task = asyncio.create_task(self._watch(), name="studio-events")

    def stop(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _peek(self) -> tuple[int, int]:
        return self.lib.rev, ring.last_id

    def _collect(self):
        """Worker-thread snapshot: (rev, songs payload if changed, new log entries)."""
        rev, log_id = self.lib.rev, ring.last_id
        payload = self.songs_payload() if rev != self._last_rev else None
        entries = ring.since(self._last_log_id, TRACE) if log_id != self._last_log_id else []
        return rev, payload, entries

    def _broadcast(self, item: tuple):
        # Per-client queues are bounded so a slow client cannot accumulate full song
        # payloads.  On overflow the *oldest* event is dropped: every `songs` event is a
        # full snapshot (the next one re-syncs the client) and a dropped `log` batch is
        # re-delivered after a reconnect via the `after` cursor - lossy by design.
        for q in list(self.clients):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:  # pragma: no cover - needs a racing consumer
                    pass

    async def _watch(self):
        while True:
            await asyncio.sleep(EVENT_TICK)
            try:
                if self.clients:
                    rev, payload, entries = await run_in_threadpool(self._collect)
                    if payload is not None:
                        self._last_rev = rev
                        self._broadcast(("songs", payload))
                    if entries:
                        self._last_log_id = entries[-1]["id"]
                        self._broadcast(("log", entries))
                else:  # nobody listening: keep the cursors warm without building payloads
                    self._last_rev, self._last_log_id = await run_in_threadpool(self._peek)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the watcher must never die
                log.exception("event watcher failed")


class Guard:
    """PR #10 hardening (Host check, upload limit, ``x-ultraai`` token on mutating
    routes) as a plain-ASGI middleware.  It must NOT be BaseHTTPMiddleware: the SSE
    endpoint polls ``Request.is_disconnected()`` on every quiet tick to reap gone
    clients, and behind BaseHTTPMiddleware that probe — a receive call inside a
    pre-cancelled scope — dies inside the middleware's internal task group and can
    never see the server's ``http.disconnect``.  A raw wrapper passes ``receive``
    through unchanged, so the probe reaches the real connection state."""

    def __init__(self, app, token: str, allowed_hosts: set[str], max_upload: int):
        self.app, self.token, self.allowed_hosts, self.max_upload = app, token, allowed_hosts, max_upload

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        h = {k.decode("latin-1").lower(): v.decode("latin-1")
             for k, v in scope.get("headers") or []}
        method, path = scope["method"], scope["path"]
        # DNS-rebinding protection: only our own host names on our port are accepted
        host = (h.get("host") or "").strip().lower()
        if host not in self.allowed_hosts:
            log.warning("refused %s %s with Host %r", method, path, host or "<missing>")
            await JSONResponse({"detail": "unexpected Host header"}, status_code=403)(scope, receive, send)
            return
        if (method == "POST" and path == "/api/songs/files"
                and (cl := h.get("content-length", "")).isdigit() and int(cl) > self.max_upload):
            log.warning("refused %s %s: Content-Length %s over the limit", method, path, cl)
            await JSONResponse({"detail": f"upload too large (max {self.max_upload // 2**30} GiB)"},
                               status_code=413)(scope, receive, send)
            return
        if method not in ("GET", "HEAD", "OPTIONS"):
            sent = h.get("x-ultraai", "").encode("utf-8", "ignore")
            if not hmac.compare_digest(sent, self.token.encode("utf-8")):
                log.warning("refused %s %s without a valid x-ultraai token", method, path)
                await JSONResponse({"detail": "missing or wrong x-ultraai token"},
                                   status_code=403)(scope, receive, send)
                return
        t0 = time.time()

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                log.log(TRACE, "%s %s -> %s (%.0f ms)", method, path, message["status"],
                        1000 * (time.time() - t0))
                if message["status"] >= 500:
                    log.error("%s %s -> %s", method, path, message["status"])
            await send(message)

        await self.app(scope, receive, guarded_send)


# ---------------------------------------------------------------- processing queue
class Queue:
    """Background threads that take queued songs to a processor, highest priority first."""

    def __init__(self, lib: Library, make_processor, slots: int = 2):
        self.lib, self.make_processor, self.slots = lib, make_processor, slots
        self._pick = threading.Lock()
        self._busy: set[str] = set()
        self._active: dict[str, object] = {}  # sid -> the processor currently running it
        self._wake = threading.Event()
        self._stop = False
        self.log: list[str] = []

    def start(self):
        for st in self.lib.all():  # resume after a restart
            if st.state == "processing" and not st.job.get("remote_id"):
                self.lib.update(st.id, state="queued", stage="")
            elif st.state not in ("processing", "downloading", "imported"):
                try:  # songs from older versions: "Artist - Title" names, URL headers
                    self.lib.rename_to_meta(st.id)
                    self.lib.apply_media_headers(st.id)
                except OSError as exc:
                    log.warning("could not rename %s: %s", st.id, exc)
        for k in range(self.slots):
            threading.Thread(target=self._loop, daemon=True, name=f"queue-{k}").start()

    def poke(self):
        self._wake.set()

    def cancel(self, sid: str) -> SongState:
        """Stop a queued or processing song; the state becomes 'error' / stage 'cancelled'."""
        st = self.lib.state(sid)
        if st.state == "queued":
            return self.lib.update(sid, state="error", stage="cancelled", error="cancelled by user")
        if st.state != "processing":
            raise HTTPException(409, f"song is not running (state {st.state!r})")
        with self._pick:
            proc = self._active.get(sid)
        if proc is not None:
            try:
                proc.cancel(sid)
            except Exception:  # noqa: BLE001 - cancelling must never fail the request
                log.exception("processor.cancel(%s) failed", sid)
        self.poke()
        return self.lib.update(sid, state="error", stage="cancelled", error="cancelled by user")

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
            st = self.lib.state(sid)
            log.info("processing %s - %s (%s)", st.artist, st.title, sid)
            t0 = time.time()
            proc = self.make_processor()
            with self._pick:
                self._active[sid] = proc
            try:
                state = proc.process(sid, stage=lambda s, sid=sid: self._progress(sid, s))
                log.info("%s - %s: %s after %.0f s", st.artist, st.title, state, time.time() - t0)
            except Cancelled:
                log.info("processing %s - %s (%s) cancelled", st.artist, st.title, sid)
                try:
                    self.lib.update(sid, state="error", stage="cancelled", error="cancelled by user", job={})
                except KeyError:
                    pass
            except Exception as exc:  # noqa: BLE001 - keep the queue alive
                log.exception("processing %s - %s failed", st.artist, st.title)
                self.log.append(traceback.format_exc()[-2000:])
                try:
                    self.lib.update(sid, state="error", stage="", error=f"{type(exc).__name__}: {exc}", job={})
                except KeyError:
                    pass
            finally:
                with self._pick:
                    self._active.pop(sid, None)
                self._busy.discard(sid)
                try:
                    self.lib.update(sid, progress={})
                except KeyError:
                    pass

    def _progress(self, sid: str, p) -> None:
        if isinstance(p, dict):
            old = self.lib.state(sid).stage
            if p.get("stage") and p.get("stage") != old:
                log.debug("%s: %s", sid, p["stage"])
            self.lib.update(sid, stage=p.get("stage") or old, progress=p)
        else:
            self.lib.update(sid, stage=p)


# ---------------------------------------------------------------- API models
class UrlIn(BaseModel):
    url: str
    options: dict | None = None


class OptionsIn(BaseModel):
    options: dict | None = None
    remember: bool = False


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
    base_version: int = 0               # optimistic concurrency: must equal the current doc_version


class KeyIn(BaseModel):
    key: str


def issue_key(i: dict) -> str:
    return f"{i.get('kind')}:{i.get('start', 0):.2f}:{(i.get('text') or '')[:24]}"


def create_app(settings: Settings, processor_factory=None, start_queue: bool = True,
               token: str | None = None, port: int = 8766) -> FastAPI:
    lib = Library(Path(settings.library))
    if token is None:
        token = load_token()  # first run: create it; impossible -> clear error, refuse to start
    if processor_factory is None:
        processor_factory = lambda: make_processor(settings, lib)  # noqa: E731
    queue = Queue(lib, processor_factory, slots=2 if settings.processor == "ssh" else 1)

    def songs_payload() -> list:
        """The /api/songs body (also pushed as `songs` SSE events when the library changes)."""
        order = {s: k for k, s in enumerate(("processing", "downloading", "needs_lyrics", "needs_review",
                                              "queued", "error", "reviewed", "imported"))}
        items = [card(s) for s in lib.all()]
        items.sort(key=lambda c: (order.get(c["state"], 9), -c["open_issues"], -c["updated"]))
        return items

    bus = EventBus(lib, songs_payload)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        bus.start()
        if start_queue:
            queue.start()
        yield
        queue.stop()
        bus.stop()

    app = FastAPI(title="UltraAI Studio", lifespan=lifespan)
    app.state.lib, app.state.queue, app.state.settings = lib, queue, settings
    app.state.token, app.state.port, app.state.bus = token, port, bus
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    app.add_middleware(Guard, token=token, allowed_hosts=allowed_hosts, max_upload=MAX_UPLOAD)

    def get_state(sid: str) -> SongState:
        try:
            return lib.state(sid)
        except KeyError:
            raise HTTPException(404, "unknown song") from None

    def card(st: SongState) -> dict:
        d = {k: getattr(st, k) for k in ("id", "artist", "title", "language", "state", "stage", "error",
                                         "source", "created", "updated", "priority", "issues",
                                         "lyrics_source", "video", "cover", "progress", "lyrics_info",
                                         "options")}
        d["open_issues"] = max(0, st.issues - len(st.dismissed))
        # the song-level check stays visible in the list until its issue is marked OK
        d["warning"] = "" if any(k.startswith("song_check:") for k in st.dismissed) else st.warning
        return d

    # ---- library / queue -----------------------------------------------------------
    @app.get("/api/songs")
    def songs():
        return songs_payload()

    @app.get("/api/events")
    async def events(request: Request, after: int = 0):
        """SSE stream (text/event-stream): a `songs` event with the full /api/songs payload
        whenever the library changes, and `log` events with new ring-buffer entries.

        Authenticated with the x-ultraai header like every other route (the web client
        owns the reconnects, so it can use fetch streaming instead of EventSource - the
        token must never reach the URL: trace logging puts query strings into the log
        ring).  The log cursor resumes from Last-Event-ID or ?after=."""
        sent = request.headers.get("x-ultraai", "").encode("utf-8", "ignore")
        if not hmac.compare_digest(sent, app.state.token.encode("utf-8")):
            log.warning("refused GET /api/events without a valid x-ultraai token")
            return JSONResponse({"detail": "missing or wrong x-ultraai token"}, status_code=403)
        last_id = (request.headers.get("last-event-id") or "").strip()
        if last_id.isdigit():
            after = max(after, int(last_id))
        if len(bus.clients) >= MAX_EVENT_CLIENTS:
            return JSONResponse({"detail": "too many event clients"}, status_code=503)

        async def stream():
            q: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
            sent_log = after
            try:
                # Initial state: snapshot the library and log backlog *before*
                # registering the queue - a broadcast racing the snapshot would be
                # delivered after (and is thus staler than) the snapshot we just sent.
                payload = await run_in_threadpool(songs_payload)
                backlog = await run_in_threadpool(ring.since, after, TRACE)
                if backlog:
                    sent_log = backlog[-1]["id"]
                bus.clients.add(q)  # removed in the finally, also on client disconnect
                yield _sse("songs", payload)
                if backlog:
                    yield _sse("log", backlog, event_id=sent_log)
                idle = 0.0
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=EVENT_TICK)
                    except asyncio.TimeoutError:
                        # Relying on Starlette cancelling the generator alone proves
                        # unreliable, so poll the connection explicitly: a client that
                        # went away costs at most one tick of bus.clients memory.
                        if await request.is_disconnected():
                            break
                        idle += EVENT_TICK
                        if idle >= EVENT_PING:
                            idle = 0.0
                            yield ": ping\n\n"  # keep the connection alive through proxies
                        continue
                    idle = 0.0
                    if kind == "log":
                        data = [e for e in data if e["id"] > sent_log]  # dedup vs the backlog
                        if not data:
                            continue
                        sent_log = data[-1]["id"]
                        yield _sse("log", data, event_id=sent_log)
                    else:
                        yield _sse("songs", data)
            finally:
                bus.clients.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/songs/url")
    def add_url(body: UrlIn):
        from .importer import import_youtube

        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "not a link")
        holder: dict = {}

        opts = body.options or dict(settings.defaults)

        def work():
            try:
                st = import_youtube(lib, url, settings=settings, options=opts,
                                    progress=lambda m: holder.get("id") and lib.update(holder["id"], stage=m))
                holder["id"] = st.id
                queue.poke()
            except Exception:  # noqa: BLE001 - state 'error' is set on the song
                pass

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    @app.post("/api/songs/files")
    async def add_files(files: list[UploadFile] = File(...), options: str = Form("")):
        import tempfile

        from .importer import import_file

        try:
            opts = json.loads(options) if options else dict(settings.defaults)
        except ValueError:
            raise HTTPException(400, "bad options") from None
        out = []
        for f in files:
            suffix = Path(f.filename or "audio").suffix.lower()
            tmp_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp_name = tmp.name
                    size = 0
                    while chunk := await f.read(1 << 20):
                        size += len(chunk)
                        if size > MAX_UPLOAD:  # abort mid-stream, not just via Content-Length
                            raise HTTPException(413, f"upload too large (max {MAX_UPLOAD // 2**30} GiB)")
                        tmp.write(chunk)
                st = import_file(lib, Path(tmp_name), original_name=f.filename, move=True)
                st = lib.update(st.id, options=opts)
                out.append(card(st))
            finally:  # never leak the temp file (import_file may have moved it away already)
                if tmp_name is not None:
                    Path(tmp_name).unlink(missing_ok=True)
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
        st = lib.update(sid, **kw)
        if "artist" in kw or "title" in kw:
            try:
                lib.rename_to_meta(sid)
            except OSError as exc:
                raise HTTPException(409, f"could not rename the song folder (file in use?): {exc}") from None
            st = lib.state(sid)
        return card(st)

    @app.get("/api/songs/{sid}/usdb")
    def usdb(sid: str):
        """The txt ready for USDB: the #VIDEO meta tags (v=<YouTube id>, co=<cover url>)
        link audio/video and cover — USDB Syncer downloads them from the links."""
        from fastapi.responses import PlainTextResponse

        from .store import usdb_text

        st = get_state(sid)
        txt = lib.txt_path(sid)
        if txt is None:
            raise HTTPException(404, "song has not been processed yet")
        return PlainTextResponse(usdb_text(txt.read_text(encoding="utf-8"), st.source))

    @app.post("/api/songs/{sid}/lyrics")
    def set_lyrics(sid: str, body: LyricsIn):
        st = get_state(sid)
        atomic_write(lib.studio_dir(sid) / "lyrics.txt", (body.text.strip() + "\n").encode("utf-8"))
        lib.update(sid, state="queued", stage="", error="", priority=max(st.priority, 1), job={})
        queue.poke()
        return {"ok": True}

    @app.post("/api/songs/{sid}/cancel")
    def cancel_song(sid: str):
        """Stop a queued or processing song (kills the local child / stops the remote unit)."""
        get_state(sid)
        st = queue.cancel(sid)
        return {"ok": True, "state": st.state, "stage": st.stage}

    @app.post("/api/songs/{sid}/process")
    def reprocess(sid: str, body: OptionsIn | None = None):
        st = get_state(sid)
        if body and body.options is not None:
            lib.update(sid, options=body.options)
        if st.state in ("processing", "downloading"):
            raise HTTPException(409, "already running")
        if not st.audio:
            raise HTTPException(400, "song has no audio")
        lib.update(sid, state="queued", stage="", error="", job={})
        queue.poke()
        return {"ok": True}

    @app.post("/api/songs/{sid}/extras")
    def extras(sid: str):
        """Download the music video / cover for a song added from a link."""
        from .importer import fetch_extras

        st = get_state(sid)
        if not st.source.startswith(("http://", "https://")):
            raise HTTPException(400, "this song was not added from a link")

        def work():
            try:
                got = fetch_extras(lib, sid, settings, progress=lambda m: lib.update(sid, stage=m))
                lib.update(sid, stage="" if got else "nothing new to download")
            except Exception as exc:  # noqa: BLE001
                lib.update(sid, stage="", error=f"video/cover download failed: {exc}")

        threading.Thread(target=work, daemon=True).start()
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
        return {"doc": song_to_doc(song), "issues": issues, "state": st.state, "dismissed": st.dismissed,
                "doc_version": st.doc_version,
                "audio": f"{base}/audio" if st.audio or song.audio else None,
                "vocals": f"{base}/vocals" if (sd / "vocals.ogg").exists() else None,
                "editor": f"{base}/editor" if (sd / "editor.json").exists() else None,
                "lyrics_source": side.get("lyrics_source", st.lyrics_source),
                "lyrics_info": side.get("lyrics") or st.lyrics_info or {}}

    @app.put("/api/songs/{sid}/doc")
    def put_doc(sid: str, body: DocIn):
        st = get_state(sid)
        txt = lib.txt_path(sid)
        if txt is None:
            raise HTTPException(404, "song has not been processed yet")
        try:
            song = doc_to_song(body.doc)
        except (DocError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, f"invalid song: {exc}") from None
        try:
            st = lib.bump_doc_version(sid, body.base_version)  # 409 on a stale base_version
        except VersionConflict as exc:
            raise HTTPException(409, f"the song changed on the server ({exc}); reload to get it") from None
        text = dumps(song)
        lib.save_version(sid, text, body.note or "edit")
        atomic_write(txt, text.encode("utf-8"))
        problems = [str(i) for i in validate(read(txt), txt.parent) if i.level in ("error", "warning")]
        return {"ok": True, "problems": problems, "saved": time.time(), "doc_version": st.doc_version}

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
    @app.get("/api/models")
    def models():
        """Choices for the processing-options window (names, rough cost on a CPU)."""
        from ..models import choices

        return choices()

    @app.post("/api/settings/defaults")
    def save_defaults(body: OptionsIn):
        settings.defaults = dict(body.options or {})
        settings.save()
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        from dataclasses import asdict

        return asdict(settings)

    @app.put("/api/settings")
    def put_settings(body: dict):
        if "log_level" in body and body["log_level"] not in LEVELS:
            raise HTTPException(400, f"log_level must be one of {sorted(LEVELS)}, got {body['log_level']!r}")
        if "cookies" in body:  # a path to the user's own cookies.txt, not the file itself
            path = str(body["cookies"] or "").strip()
            if path:
                path = str(Path(path).expanduser().resolve())
                if not Path(path).is_file():  # codeql[py/path-injection] - user's own settings field
                    raise HTTPException(400, "cookies path must name an existing file")
            body["cookies"] = path
        for k, v in body.items():
            if k not in Settings.__dataclass_fields__ or k == "extra":
                continue
            cur = getattr(settings, k)
            if isinstance(cur, bool):
                if isinstance(v, bool):
                    pass
                elif isinstance(v, str) and v.strip().lower() in ("true", "false"):
                    v = v.strip().lower() == "true"
                else:
                    raise HTTPException(400, f"{k}: expected a boolean, got {v!r}")
            elif isinstance(cur, int):
                if isinstance(v, bool) or not isinstance(v, int):
                    if isinstance(v, str) and v.strip() and v.strip().lstrip("+-").isdigit():
                        v = int(v)
                    else:
                        raise HTTPException(400, f"{k}: expected an integer, got {v!r}")
            elif isinstance(cur, str):
                if v is None:  # JSON null means "empty", not the string "None"
                    v = ""
                elif not isinstance(v, str):
                    raise HTTPException(400, f"{k}: expected a string, got {v!r}")
            setattr(settings, k, v)
        settings.save()
        if "log_level" in body:
            from .logs import set_level

            set_level(settings.log_level)
        log.info("settings saved: %s", ", ".join(sorted(body)))
        return {"ok": True, "restart_needed": "library" in body}

    @app.post("/api/settings/test")
    def test_connection():
        p = make_processor(settings, lib)
        try:
            return {"ok": True, "result": p.check() if hasattr(p, "check") else "local"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @app.get("/api/log")
    def get_log(after: int = 0, level: str = "info"):
        return ring.since(after, LEVELS.get(level, logging.INFO))

    @app.get("/api/songs/{sid}/worker-log")
    def worker_log(sid: str):
        get_state(sid)
        f = lib.folder(sid) / ".studio" / "worker.log"
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(f.read_text(encoding="utf-8", errors="replace") if f.exists() else "")

    if WEB.exists():
        from fastapi.staticfiles import StaticFiles

        @app.get("/", include_in_schema=False)
        def index():
            """index.html with the per-install API token injected (the UI sends it as x-ultraai)."""
            html = (WEB / "index.html").read_text(encoding="utf-8")
            meta = f'<meta name="ultraai-token" content="{token}">'
            if "<meta name=\"ultraai-token\"" not in html:
                html = html.replace("<head>", "<head>\n" + meta, 1)
            return HTMLResponse(html)

        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def make_processor(settings: Settings, lib: Library):
    from .processors import LocalProcessor, SshProcessor, SshTransport

    sep = settings.extra.get("separation") or settings.separation
    if settings.processor == "local":
        return LocalProcessor(lib, separation=sep, cookies=getattr(settings, "cookies", ""))
    if not settings.ssh_host:
        raise RuntimeError("no server configured (Settings: SSH host)")
    return SshProcessor(lib, SshTransport(settings.ssh_host, settings.ssh_port or None),
                        settings.remote_dir, settings.remote_ultraai, separation=sep,
                        cookies=getattr(settings, "cookies", ""))


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_window_or_browser(url: str) -> None:
    """Own app window (pywebview / Edge WebView2); the default browser if that is missing."""
    try:
        import webview

        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # "Save as file…" in the app window
        except Exception:  # noqa: BLE001 - older pywebview
            pass
        webview.create_window("UltraAI Studio", url, width=1400, height=900, min_size=(900, 600))
        webview.start()
    except Exception:  # noqa: BLE001 - no WebView2 / no GUI toolkit: use the browser
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def run(port: int = 8766, open_window: bool = True) -> None:
    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    if _port_in_use(port):  # already running (second start): just show it
        if open_window:
            open_window_or_browser(url)
        return
    settings = Settings.load()
    from .config import config_dir
    from .logs import setup

    setup(settings.log_level, config_dir())
    log.info("UltraAI Studio on %s, library %s, processing %s %s", url, settings.library, settings.processor,
             settings.ssh_host)
    app = create_app(settings, port=port)  # creates/loads the API token; refuses to start without one
    if not open_window:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
        return
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           log_config=None))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        if not th.is_alive():
            raise RuntimeError(f"the Studio server could not start on port {port}")
        time.sleep(0.05)
    open_window_or_browser(url)
    server.should_exit = True
