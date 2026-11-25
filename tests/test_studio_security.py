"""Security tests for the Studio API: per-install token, Host check, upload limit,
optimistic doc concurrency, settings coercion, job cancellation, quarantine + atomic
versioning, remote-command quoting.  Everything runs on a tiny fake library in
tmp_path — no songs/ folder, no network, no SSH."""

import json
import os
import shlex
import stat
import tempfile
import threading
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings, config_dir, load_token, token_path  # noqa: E402
from ultraai.studio.processors import _PROCS, Cancelled, LocalProcessor, SshProcessor  # noqa: E402
from ultraai.studio.store import Library, VersionConflict  # noqa: E402

PORT = 8766
TOKEN = "test-token-abcdef0123456789"
BASE = f"http://127.0.0.1:{PORT}"
H = {"x-ultraai": TOKEN}

MIN_SONG = """#TITLE:Test Song
#ARTIST:Tester
#BPM:300
#GAP:500
#MP3:{name}.wav
: 0 4 0 La
: 4 4 2 la
- 12
E
"""


def _wav(path: Path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)


def _make_song(lib: Library, artist="Tester", title="Test Song") -> str:
    """A song folder with audio + txt, as if it had been processed already."""
    st = lib.create(artist, title)
    folder = lib.folder(st.id)
    name = folder.name
    (folder / f"{name}.wav").write_bytes(b"RIFF" + b"\0" * 60)
    (folder / f"{name}.txt").write_text(MIN_SONG.format(name=name), encoding="utf-8")
    lib.update(st.id, audio=f"{name}.wav", state="imported")
    return st.id


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Isolate the config dir (token.txt, studio.json) into tmp_path."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


@pytest.fixture
def studio(cfg):
    """App + client with a fixed token, no queue threads (songs stay queued)."""
    settings = Settings(library=str(cfg / "lib"), processor="local")
    app = create_app(settings, processor_factory=lambda: LocalProcessor(app.state.lib),
                     start_queue=False, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


def _upload(client, tmp_path, name="Some Artist - Some Song.wav"):
    f = tmp_path / name
    _wav(f)
    r = client.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))], headers=H)
    assert r.status_code == 200, r.text
    return r.json()[0]["id"]


def _wait_state(client, sid, want, timeout=10):
    t = time.time() + timeout
    while time.time() < t:
        s = client.get(f"/api/songs/{sid}").json()
        if s["state"] in want:
            return s
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {want}, state={s['state']} error={s.get('error')}")


# ---------------------------------------------------------------- token + host guard
def test_mutations_require_the_token(studio, cfg):
    sid = _make_song(studio.app.state.lib)
    assert studio.post(f"/api/songs/{sid}/reviewed").status_code == 403          # missing
    assert studio.post(f"/api/songs/{sid}/reviewed", headers={"x-ultraai": "wrong"}).status_code == 403
    assert studio.post(f"/api/songs/{sid}/reviewed", headers={"x-ultraai": "1"}).status_code == 403  # old constant
    r = studio.post(f"/api/songs/{sid}/reviewed", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "reviewed"
    # GETs stay open (no token needed)
    assert studio.get("/api/songs").status_code == 200
    assert studio.get(f"/api/songs/{sid}/doc").status_code == 200


def test_index_injects_the_token(studio):
    r = studio.get("/")
    assert r.status_code == 200
    assert f'<meta name="ultraai-token" content="{TOKEN}">' in r.text
    # static assets are still served from the same mount
    assert studio.get("/api.js").status_code == 200


def test_host_header_validation(studio, cfg):
    sid = _make_song(studio.app.state.lib)
    for host in ("evil.example.com", "127.0.0.1:9999", "localhost:80", "", "[::1]:8766x"):
        assert studio.get("/api/songs", headers={"host": host}).status_code == 403, host
        assert studio.post(f"/api/songs/{sid}/reviewed", headers={**H, "host": host}).status_code == 403, host
    for host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"):
        assert studio.get("/api/songs", headers={"host": host}).status_code == 200, host


def test_token_file_created_once_mode_0600(cfg):
    p = token_path()
    assert not p.exists()
    tok = load_token()
    assert p.exists() and p.read_text(encoding="utf-8").strip() == tok
    assert len(tok) >= 32
    if os.name != "nt":  # Windows has no real permission bits
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load_token() == tok  # stable across calls
    # an app without an explicit token picks it up from the file
    settings = Settings(library=str(cfg / "lib"), processor="local")
    app = create_app(settings, start_queue=False, port=PORT)
    assert app.state.token == tok
    with TestClient(app, base_url=BASE) as client:
        assert client.post("/api/songs/url", json={"url": "https://x"},
                           headers={"x-ultraai": tok}).status_code in (200, 400, 409)
        assert client.post("/api/songs/url", json={"url": "https://x"}).status_code == 403


def test_refuses_to_start_without_a_token_file(cfg, monkeypatch):
    blocker = cfg / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(blocker))  # config_dir() cannot be created under a file
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))
    with pytest.raises(RuntimeError, match="token"):
        load_token()
    with pytest.raises(RuntimeError, match="token"):
        create_app(Settings(library=str(cfg / "lib")), start_queue=False, port=PORT)


# ---------------------------------------------------------------- doc optimistic concurrency
def test_doc_version_conflict(studio):
    sid = _make_song(studio.app.state.lib)
    d = studio.get(f"/api/songs/{sid}/doc").json()
    assert d["doc_version"] == 0
    doc = d["doc"]
    doc["voices"][0]["lines"][0]["notes"][0]["text"] = "Changed"
    # stale base_version -> 409
    r = studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "note": "t", "base_version": 5}, headers=H)
    assert r.status_code == 409
    # correct base_version -> ok, version bumped
    r = studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "note": "t", "base_version": 0}, headers=H)
    assert r.status_code == 200 and r.json()["doc_version"] == 1, r.text
    d2 = studio.get(f"/api/songs/{sid}/doc").json()
    assert d2["doc_version"] == 1
    assert d2["doc"]["voices"][0]["lines"][0]["notes"][0]["text"] == "Changed"
    # the old base_version is now stale
    assert studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "base_version": 0},
                      headers=H).status_code == 409
    # and the new one works
    r = studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "base_version": 1}, headers=H)
    assert r.status_code == 200 and r.json()["doc_version"] == 2


# ---------------------------------------------------------------- settings coercion
def test_settings_put_coercion(studio):
    get = lambda: studio.get("/api/settings").json()  # noqa: E731
    # JSON null for a string field: empty string, not "None"
    assert studio.put("/api/settings", json={"ssh_host": None}, headers=H).status_code == 200
    assert get()["ssh_host"] == ""
    # "false" must not become True for bools
    assert studio.put("/api/settings", json={"youtube_cover": "false"}, headers=H).status_code == 200
    assert get()["youtube_cover"] is False
    assert studio.put("/api/settings", json={"youtube_cover": False}, headers=H).status_code == 200
    assert get()["youtube_cover"] is False
    assert studio.put("/api/settings", json={"youtube_cover": "true"}, headers=H).status_code == 200
    assert get()["youtube_cover"] is True
    assert studio.put("/api/settings", json={"youtube_cover": None}, headers=H).status_code == 400
    # invalid int -> 400, not 500; numeric string and real int are fine
    assert studio.put("/api/settings", json={"ssh_port": "abc"}, headers=H).status_code == 400
    assert studio.put("/api/settings", json={"ssh_port": None}, headers=H).status_code == 400
    assert studio.put("/api/settings", json={"ssh_port": 2222}, headers=H).status_code == 200
    assert get()["ssh_port"] == 2222
    assert studio.put("/api/settings", json={"ssh_port": "2022"}, headers=H).status_code == 200
    assert get()["ssh_port"] == 2022
    # log_level validated against logs.LEVELS
    assert studio.put("/api/settings", json={"log_level": "verbose"}, headers=H).status_code == 400
    assert studio.put("/api/settings", json={"log_level": "debug"}, headers=H).status_code == 200
    assert get()["log_level"] == "debug"
    # unknown keys are ignored, defaults (a dict) still works
    assert studio.put("/api/settings", json={"nonsense": 1, "defaults": {"a": 1}}, headers=H).status_code == 200


# ---------------------------------------------------------------- upload limit + temp cleanup
def test_upload_content_length_limit(studio, cfg, tmp_path):
    f = tmp_path / "a.wav"
    _wav(f)
    r = studio.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))],
                    headers={**H, "Content-Length": str(3 * 2**30)})
    assert r.status_code == 413


def test_upload_mid_stream_abort_and_temp_cleanup(studio, cfg, tmp_path, monkeypatch):
    import ultraai.studio.app as app_mod

    monkeypatch.setattr(app_mod, "MAX_UPLOAD", 1024)
    boundary = "----pytestboundary"
    body = ((f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"big.wav\"\r\n"
             f"Content-Type: audio/wav\r\n\r\n").encode() + b"x" * 8192 + f"\r\n--{boundary}--\r\n".encode())
    # small Content-Length (passes the middleware check) but a bigger stream: abort mid-stream
    r = studio.post("/api/songs/files", content=body,
                    headers={**H, "Content-Type": f"multipart/form-data; boundary={boundary}",
                             "Content-Length": "100"})
    assert r.status_code == 413, r.text


def test_upload_failure_leaves_no_temp_file(studio, cfg, tmp_path, monkeypatch):
    created = []
    real = tempfile.NamedTemporaryFile

    def spy(*a, **kw):
        f = real(*a, **kw)
        created.append(Path(f.name))
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", spy)

    def boom(*a, **kw):
        raise RuntimeError("import failed")

    monkeypatch.setattr("ultraai.studio.importer.import_file", boom)
    studio._transport.raise_server_exceptions = False  # we want the 500 response, not the raised error
    f = tmp_path / "a.wav"
    _wav(f)
    r = studio.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))], headers=H)
    assert r.status_code == 500
    assert created and all(not p.exists() for p in created)


# ---------------------------------------------------------------- cancellation
def test_cancel_queued_song(studio, cfg, tmp_path):
    sid = _upload(studio, tmp_path)
    assert studio.get(f"/api/songs/{sid}").json()["state"] == "queued"
    r = studio.post(f"/api/songs/{sid}/cancel", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "error" and r.json()["stage"] == "cancelled"
    s = studio.get(f"/api/songs/{sid}").json()
    assert s["state"] == "error" and s["stage"] == "cancelled"
    # cancelling again / an unknown song
    assert studio.post(f"/api/songs/{sid}/cancel", headers=H).status_code == 409
    assert studio.post("/api/songs/nope/cancel", headers=H).status_code == 404


def test_cancel_processing_song(cfg, tmp_path):
    """A processing song is stopped through its processor (Cancelled -> error/cancelled)."""
    settings = Settings(library=str(cfg / "libq"), processor="local")
    cancelled = set()
    started = threading.Event()

    class FakeProcessor:
        def process(self, sid, stage=None):
            started.set()
            while sid not in cancelled:
                time.sleep(0.05)
            raise Cancelled(sid)

        def cancel(self, sid):
            cancelled.add(sid)

    app = create_app(settings, processor_factory=FakeProcessor, start_queue=True, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        sid = _upload(client, tmp_path)
        assert started.wait(10)
        s = _wait_state(client, sid, ("processing",))
        assert s["state"] == "processing"
        r = client.post(f"/api/songs/{sid}/cancel", headers=H)
        assert r.status_code == 200 and r.json()["stage"] == "cancelled"
        s = _wait_state(client, sid, ("error",))
        assert s["stage"] == "cancelled"


# ---------------------------------------------------------------- store: quarantine, versions, doc_version
def test_corrupt_song_json_is_quarantined(cfg, studio):
    lib = studio.app.state.lib
    sid = _make_song(lib)
    f = lib.folder(sid) / ".studio" / "song.json"
    f.write_text("{not json", encoding="utf-8")
    st = lib.state(sid)  # must not crash; falls back to the imported default
    assert st.id == sid and st.state == "imported"
    assert not f.exists()
    assert list((lib.folder(sid) / ".studio").glob("song.json.corrupt-*"))
    # and a rescan still finds the folder (via its txt)
    assert any(s.id for s in lib.all())


def test_corrupt_settings_json_is_quarantined(cfg):
    p = config_dir() / "studio.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    s = Settings.load()
    assert s == Settings()  # defaults, no crash
    assert not p.exists()
    assert list(p.parent.glob("studio.json.corrupt-*"))


def test_versions_numbered_max_plus_one_and_atomic(cfg):
    lib = Library(cfg / "libv")
    sid = _make_song(lib)
    v = lib.studio_dir(sid) / "versions"
    assert lib.save_version(sid, "a", "auto").name == "000-auto.txt"
    assert lib.save_version(sid, "b", "edit").name == "001-edit.txt"
    (v / "000-auto.txt").unlink()  # a deleted version must not cause a number reuse
    assert lib.save_version(sid, "c", "edit").name == "002-edit.txt"
    assert not list(v.glob("*.tmp"))  # atomic write leaves no temp files
    assert lib.versions(sid) and len(lib.versions(sid)) == 2


def test_bump_doc_version_conflict(cfg):
    lib = Library(cfg / "libd")
    sid = _make_song(lib)
    assert lib.state(sid).doc_version == 0
    st = lib.bump_doc_version(sid, 0)
    assert st.doc_version == 1
    with pytest.raises(VersionConflict):
        lib.bump_doc_version(sid, 0)
    assert lib.bump_doc_version(sid, 1).doc_version == 2


def test_ingest_bumps_doc_version(cfg):
    lib = Library(cfg / "libi")
    sid = _make_song(lib)
    out = cfg / "out"
    out.mkdir()
    (out / "song.txt").write_text(MIN_SONG.format(name=lib.folder(sid).name), encoding="utf-8")
    (out / "song.ultraai.json").write_text(json.dumps({"issues": [], "lyrics_source": "LRCLIB"}),
                                            encoding="utf-8")
    state = LocalProcessor(lib).ingest(sid, out, {"state": "done"})
    assert state == "needs_review"
    assert lib.state(sid).doc_version == 1  # reprocess invalidates open editors (409 on save)


# ---------------------------------------------------------------- processors: whitelist, quoting, cancel
def test_job_spec_audio_whitelist(cfg):
    lib = Library(cfg / "libw")
    sid = _make_song(lib)
    p = LocalProcessor(lib)
    assert p.job_spec(sid, "j1")["audio"] == "audio.wav"
    lib.update(sid, audio="evil.sh")
    with pytest.raises(ValueError, match="audio"):
        p.job_spec(sid, "j1")


class _RecTransport:
    """Records the remote commands instead of running them."""

    def __init__(self):
        self.cmds = []

    def run(self, cmd, timeout=120):
        self.cmds.append(cmd)
        return "ok\n"

    def stream(self, cmd):
        self.cmds.append(cmd)
        yield from ()

    def put(self, local, remote):
        self.cmds.append(f"put {remote}")

    def get_dir(self, remote, local):
        self.cmds.append(f"get {remote}")


def test_ssh_check_quotes_paths_and_reaps_old_jobs(cfg):
    lib = Library(cfg / "libs")
    _make_song(lib)
    rec = _RecTransport()
    p = SshProcessor(lib, rec, remote_dir="my dir", ultraai="a b/ultraai")
    assert p.check() == "ok"
    reap, probe = rec.cmds[0], rec.cmds[1]
    assert reap.startswith(f"find {shlex.quote('my dir/jobs')} ")
    assert "-mmin +1440" in reap and "rm -rf" in reap  # older than 24 h
    assert shlex.quote("my dir") in probe and shlex.quote("a b/ultraai") in probe
    assert "'a b/ultraai'" in probe  # actually quoted, not just present


def test_ssh_cancel_stops_the_systemd_unit(cfg):
    lib = Library(cfg / "libc")
    sid = _make_song(lib)
    rec = _RecTransport()
    p = SshProcessor(lib, rec, remote_dir="UltraAI", ultraai="ultraai")
    lib.update(sid, state="processing", job={"remote_id": "abc123"})
    p.cancel(sid)
    assert p._was_cancelled(sid)
    assert any("systemctl --user stop" in c and "ultraai-job-abc123.service" in c for c in rec.cmds)


def test_local_cancel_kills_the_child(cfg):
    lib = Library(cfg / "libl")
    sid = _make_song(lib)
    p = LocalProcessor(lib)

    class FakePopen:
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    fake = FakePopen()
    _PROCS[sid] = fake
    try:
        p.cancel(sid)
        assert fake.killed and p._was_cancelled(sid)
    finally:
        _PROCS.pop(sid, None)
