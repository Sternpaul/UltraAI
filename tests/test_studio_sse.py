"""GET /api/events (SSE): `songs` events when the library changes, `log` events for new
ring-buffer entries, x-ultraai header auth (no token in the URL - query strings surface
in trace-level request logs), Last-Event-ID / ?after= resume of the log cursor, the
client cap and bounded per-client queues, and deterministic disconnect cleanup: the
endpoint polls `request.is_disconnected()` on every quiet tick and drops its queue
there, so cleanup does not depend on the ASGI server cancelling the generator.

Everything runs on a tiny fake library in tmp_path - no songs/ folder, no network, no
SSH.  The disconnect cleanup is driven through a raw ASGI (scope, receive, send) call:
TestClient never reports a disconnect for an intentionally endless stream, which is
exactly how the flake this replaces was born."""

import asyncio
import json
import logging
import queue as queue_mod
import threading
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import EVENT_QUEUE_MAX, MAX_EVENT_CLIENTS, EventBus, create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.logs import TRACE, ring  # noqa: E402

PORT = 8766
TOKEN = "test-token-sse-0123456789abcdef"
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"x-ultraai": TOKEN}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Isolate the config dir (token.txt, studio.json) into tmp_path."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


@pytest.fixture
def logcap():
    """Attach the in-memory ring handler and let TRACE through, so log records reach it."""
    root = logging.getLogger()
    added = ring not in root.handlers
    if added:
        root.addHandler(ring)
    old_level = root.level
    root.setLevel(TRACE)
    try:
        yield ring
    finally:
        root.setLevel(old_level)
        if added:
            root.removeHandler(ring)


@pytest.fixture
def studio(cfg, logcap):
    """App + client with a fixed token, no queue threads (songs stay queued)."""
    settings = Settings(library=str(cfg / "lib"), processor="local")
    app = create_app(settings, processor_factory=lambda: None, start_queue=False, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


def sse_app(tmp_path):
    """Same app as the `studio` fixture, but for raw-ASGI calls (no TestClient lifespan)."""
    settings = Settings(library=str(tmp_path / "lib"), processor="local")
    return create_app(settings, processor_factory=lambda: None, start_queue=False, token=TOKEN, port=PORT)


class SSEReader:
    """Parses an open streaming response in a daemon thread; events land in a queue."""

    def __init__(self, resp):
        self.events: queue_mod.Queue = queue_mod.Queue()
        self._thread = threading.Thread(target=self._run, args=(resp,), daemon=True)
        self._thread.start()

    def _run(self, resp):
        ev = {"event": "message", "id": None, "data": []}
        try:
            for line in resp.iter_lines():
                if not line:
                    if ev["data"]:
                        try:
                            data = json.loads("\n".join(ev["data"]))
                        except ValueError:
                            data = None
                        self.events.put((ev["event"], ev["id"], data))
                    ev = {"event": "message", "id": None, "data": []}
                    continue
                if line.startswith(":"):  # heartbeat comment
                    continue
                k, _, v = line.partition(":")
                v = v[1:] if v.startswith(" ") else v
                if k == "event":
                    ev["event"] = v
                elif k == "id":
                    ev["id"] = v
                elif k == "data":
                    ev["data"].append(v)
        except Exception:  # noqa: BLE001 - the stream ended / was closed
            pass
        finally:
            self.events.put(None)

    def next(self, timeout=5.0):
        return self.events.get(timeout=timeout)

    def collect(self, timeout=3.0):
        out, deadline = [], time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                item = self.events.get(timeout=remaining)
            except queue_mod.Empty:
                break
            if item is None:
                break
            out.append(item)
        return out


# ---------------------------------------------------------------- token (header, GET too)
def test_events_token_via_x_ultraai_header_only(studio):
    assert studio.get("/api/events").status_code == 403                       # missing header
    assert studio.get("/api/events", headers={"x-ultraai": "wrong"}).status_code == 403
    # the old query-param path is gone for good: a token in the URL would surface in
    # trace-level access logs (uvicorn logs the query string)
    assert studio.get("/api/events", params={"token": TOKEN}).status_code == 403
    with studio.stream("GET", "/api/events", headers=AUTH) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
    # header auth still guards the non-GET routes
    assert studio.post("/api/songs/nope/reviewed").status_code == 403
    assert studio.post("/api/songs/nope/reviewed", headers=AUTH).status_code == 404


# ---------------------------------------------------------------- songs events
def test_songs_event_on_library_change(studio):
    lib = studio.app.state.lib
    with studio.stream("GET", "/api/events", headers=AUTH) as resp:
        assert resp.status_code == 200
        r = SSEReader(resp)
        ev, _eid, data = r.next(5)                      # initial songs event: empty library
        assert ev == "songs" and data == []
        holder = {}

        def mutate():                                   # the store is thread-safe
            time.sleep(0.2)
            holder["t"] = time.time()
            st = lib.create("SSE Artist", "SSE Song")
            lib.update(st.id, state="imported")
            holder["sid"] = st.id

        th = threading.Thread(target=mutate)
        th.start()
        got = None
        deadline = time.time() + 5
        while time.time() < deadline:
            item = r.next(5)
            if item is None:
                break
            ev, _eid, data = item
            if ev == "songs" and any(s["id"] == holder.get("sid") for s in (data or [])):
                got = (data, time.time())
                break
        th.join()
        assert got, "no `songs` event after the library change"
        assert got[1] - holder["t"] < 2.5, f"songs event took {got[1] - holder['t']:.2f} s"
        assert any(s["id"] == holder["sid"] and s["artist"] == "SSE Artist" for s in got[0])


# ---------------------------------------------------------------- log events + cursor resume
def test_log_event_on_new_entry(studio):
    with studio.stream("GET", "/api/events", headers=AUTH) as resp:
        r = SSEReader(resp)
        r.next(5)                                       # initial songs event
        marker = f"hello-sse-{time.time()}"
        logging.getLogger("ultraai.sse-test").warning(marker)
        found = None
        deadline = time.time() + 5
        while time.time() < deadline:
            item = r.next(5)
            if item is None:
                break
            ev, eid, data = item
            if ev == "log" and any(marker in (e.get("msg") or "") for e in (data or [])):
                found = (eid, data)
                break
        assert found, "no `log` event after the log entry"
        assert found[0] == str(found[1][-1]["id"])      # the SSE id is the log cursor


def test_log_resume_from_last_event_id_and_after_param(studio):
    lg = logging.getLogger("ultraai.sse-resume")
    lg.warning("resume marker A")
    a = ring.last_id
    lg.warning("resume marker B")
    b = ring.last_id
    lg.warning("resume marker C")
    c = ring.last_id
    assert a < b < c

    def ids_from(headers=None, params=None):
        with studio.stream("GET", "/api/events", params=params, headers={**AUTH, **(headers or {})}) as resp:
            assert resp.status_code == 200
            events = SSEReader(resp).collect(3)
        return {e["id"] for ev, _eid, data in events if ev == "log" for e in (data or [])}

    ids = ids_from(headers={"Last-Event-ID": str(b)})   # browser resend on reconnect
    assert c in ids and a not in ids and b not in ids
    ids = ids_from(params={"after": b})                 # explicit query param
    assert c in ids and a not in ids and b not in ids


# ---------------------------------------------------------------- concurrency: cap + cleanup
def test_event_client_cap(studio):
    bus = studio.app.state.bus
    dummies = [asyncio.Queue() for _ in range(MAX_EVENT_CLIENTS)]
    bus.clients.update(dummies)
    try:
        r = studio.get("/api/events", headers=AUTH)
        assert r.status_code == 503
    finally:
        for d in dummies:
            bus.clients.discard(d)


def test_client_queue_is_bounded_drop_oldest():
    """A slow client cannot accumulate events: the oldest are dropped on overflow (every
    `songs` event is a full snapshot, so the newest event is always the important one)."""
    bus = EventBus(lib=None, songs_payload=None)
    q: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
    bus.clients.add(q)
    for k in range(EVENT_QUEUE_MAX + 10):
        bus._broadcast(("songs", [k]))
    assert q.qsize() == EVENT_QUEUE_MAX
    assert q.get_nowait() == ("songs", [10])      # the 10 oldest were dropped, newest kept


# ---------------------------------------------------------------- disconnect cleanup (raw ASGI)
def _drive_sse(app, receive):
    """Call the app as a live server would; receive controls what the ASGI side sees."""
    frames: list[dict] = []

    async def send(message):
        frames.append(message)

    scope = {"type": "http", "http_version": "1.1", "method": "GET", "path": "/api/events",
             "raw_path": b"/api/events", "root_path": "", "scheme": "http", "query_string": b"",
             "headers": [(b"host", f"127.0.0.1:{PORT}".encode()), (b"x-ultraai", TOKEN.encode())],
             "client": ("testclient", 50000), "server": ("127.0.0.1", PORT)}

    async def scenario():
        try:
            await asyncio.wait_for(app(scope, receive, send), timeout=30)
        except asyncio.TimeoutError:
            raise AssertionError("the SSE stream did not end after the client went away")

    t0 = time.time()
    asyncio.run(scenario())
    body = b"".join(m.get("body", b"") for m in frames if m["type"] == "http.response.body")
    return time.time() - t0, [m for m in frames if m["type"] == "http.response.start"][0]["status"], body


def test_client_queue_removed_on_disconnect(tmp_path, logcap):
    """The normal path: the server relays http.disconnect, the generator is cancelled and
    the queue is gone from bus.clients - no polling threads, no flake."""
    app = sse_app(tmp_path)
    bus = app.state.bus
    seen = {"request": False}

    async def receive():
        if not seen["request"]:
            seen["request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    dt, status, body = _drive_sse(app, receive)
    assert status == 200                                # the cancel may beat the first frame
    assert not bus.clients
    assert dt < 5, f"disconnect cleanup took {dt:.2f} s"


def test_client_queue_removed_on_disconnect_ping_path(tmp_path, logcap):
    """The path the review was about: the server's cancellation does not reach the
    generator (Starlette's disconnect listener is parked), so only the endpoint's own
    `request.is_disconnected()` check on the quiet tick drops the queue."""
    app = sse_app(tmp_path)
    bus = app.state.bus
    seen = {"request": False, "parked": False}
    parked = asyncio.Event()

    async def receive():
        if not seen["request"]:
            seen["request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if not seen["parked"]:      # the first caller parks: Starlette's cancel listener
            seen["parked"] = True
            await parked.wait()     # (it never learns about the disconnect in this test)
            return {"type": "http.disconnect"}
        return {"type": "http.disconnect"}

    def cleanup():
        parked.set()                # un-park the listener at teardown

    try:
        dt, status, body = _drive_sse(app, receive)
        assert status == 200 and b"event: songs" in body
        assert not bus.clients
        # the endpoint noticed on its first quiet tick (EVENT_TICK = 1 s)
        assert dt < 6, f"disconnect cleanup took {dt:.2f} s"
    finally:
        cleanup()


# ---------------------------------------------------------------- backward compat
def test_songs_and_log_endpoints_still_work(studio):
    lib = studio.app.state.lib
    st = lib.create("Compat", "Song")
    r = studio.get("/api/songs")
    assert r.status_code == 200 and any(s["id"] == st.id for s in r.json())
    logging.getLogger("ultraai.sse-compat").warning("compat marker")
    r = studio.get("/api/log", params={"after": 0, "level": "warning"})
    assert r.status_code == 200 and any("compat marker" in (e["msg"] or "") for e in r.json())
