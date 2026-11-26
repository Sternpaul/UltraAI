"""Live worker log while a song is processing (local AND ssh).

The steps dialog's GET /api/songs/{sid}/worker-log used to be empty until the job
finished.  Now a background tailer (LiveLogTailer) appends the running job's log bytes
into .studio/worker.log every ~2 s — read from the local job dir on the same disk, or via
``tail -c +<offset>`` over ssh — and the endpoint supports ``?after=<bytes>`` incremental
polling with an X-Total-Size header.

Everything runs on a tiny fake library in tmp_path: no songs/ folder, no network, and the
"ssh" side is ShellTransport (bash) or a fake run() — never a real ssh, never systemd.
"""

import json
import re
import shutil
import threading
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import LiveLogTailer, LocalProcessor, SshProcessor, local_log_source, ssh_log_source  # noqa: E402
from ultraai.studio.store import Library  # noqa: E402

PORT = 8766
TOKEN = "test-token-livelog-0123456789"
BASE = f"http://127.0.0.1:{PORT}"
H = {"x-ultraai": TOKEN}


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "lib")


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """App + client with a fixed token, no queue threads (songs stay queued)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    settings = Settings(library=str(tmp_path / "lib"), processor="local")
    app = create_app(settings, processor_factory=lambda: LocalProcessor(app.state.lib),
                     start_queue=False, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


def _wav(path: Path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)


def _wait_for(predicate, timeout=10, what="condition"):
    t = time.time() + timeout
    while time.time() < t:
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for {what}")


# ---------------------------------------------------------------- tailer: local
def test_local_tailer_streams_while_job_runs(lib, tmp_path):
    sid = lib.create("Tester", "Live Log Song").id
    job = tmp_path / "job"
    (job / "out").mkdir(parents=True)
    src = job / "out" / "worker.log"
    err = job / "stderr.txt"
    src.write_text("chunk1\n")

    stop_writing = threading.Event()

    def writer():
        for i in range(2, 6):
            if stop_writing.is_set():
                return
            with open(src, "a") as f:
                f.write(f"chunk{i}\n")
            with open(err, "a") as f:
                f.write(f"err{i}\n")
            time.sleep(0.05)

    th = threading.Thread(target=writer, daemon=True)
    th.start()

    tailer = LiveLogTailer(lib, sid, local_log_source(job), interval=0.05).start()
    dest = lib.studio_dir(sid) / "worker.log"
    try:
        _wait_for(lambda: "chunk5" in dest.read_text(encoding="utf-8"), what="chunk5 in the live log")
    finally:
        stop_writing.set()
        tailer.stop()
        th.join(timeout=2)
    content = dest.read_text(encoding="utf-8")
    assert "chunk1" in content and "chunk5" in content and "err4" in content  # streamed BEFORE the job ended
    assert tailer.offset == src.stat().st_size + err.stat().st_size  # tracked byte offset == file size seen
    assert tailer.thread is None or not tailer.thread.is_alive()  # stop() ended the thread

    # the end-of-job full copy stays the authoritative final overwrite
    src.write_text("FINAL authoritative log\n")
    shutil.copy2(src, dest)
    assert dest.read_text(encoding="utf-8") == "FINAL authoritative log\n"


def test_local_tailer_injected_sleep_and_stop(lib, tmp_path):
    sid = lib.create("Tester", "Sleep Song").id
    job = tmp_path / "job"
    job.mkdir()
    sleeps = []
    tailer = LiveLogTailer(lib, sid, local_log_source(job), interval=0.01,
                           sleep=lambda s: (sleeps.append(s), time.sleep(min(s, 0.01)))[0]).start()
    time.sleep(0.15)
    tailer.stop()
    assert sleeps and all(s == 0.01 for s in sleeps)  # the injected sleep drove the ticks
    assert tailer.thread is None or not tailer.thread.is_alive()
    assert (lib.studio_dir(sid) / "worker.log").read_bytes() == b""  # fresh, empty log for the run


def test_local_source_later_file_failure_keeps_earlier_bytes(tmp_path, monkeypatch):
    """One unreadable file must not drop the other files' bytes of the same tick."""
    job = tmp_path / "job"
    (job / "out").mkdir(parents=True)
    (job / "out" / "worker.log").write_bytes(b"AAA\n")
    (job / "stderr.txt").write_bytes(b"BBB\n")
    (job / "log.txt").write_bytes(b"CCC\n")
    src = local_log_source(job)
    real_open = open

    def flaky(file, mode="r", *a, **kw):
        if "b" in mode and str(file).endswith("log.txt"):
            raise PermissionError(13, "file in use")
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr("builtins.open", flaky)
    chunk, size = src(0)  # log.txt unreadable this tick; its offset stays, the others commit
    assert chunk == b"AAA\nBBB\n"
    assert size == len(b"AAA\n") + len(b"BBB\n")
    monkeypatch.undo()
    chunk, size = src(size)  # log.txt retried from its unchanged offset
    assert chunk == b"CCC\n" and size == len(b"AAA\nBBB\nCCC\n")


# ---------------------------------------------------------------- tailer: ssh (fake run, never a real ssh)
class FakeRemote:
    """Stands in for SshTransport.run: serves `wc -c` + `tail -c +N` from in-memory files."""

    def __init__(self, files: dict[str, bytes]):
        self.files = {k: bytearray(v) for k, v in files.items()}
        self.cmds: list[str] = []
        self.failures = 0
        self.fail_sub = None  # substring of cmd that makes the next matching run raise once

    def run(self, cmd: str, timeout: float = 120) -> str:
        self.cmds.append(cmd)
        if self.fail_sub and self.fail_sub in cmd:
            self.fail_sub = None
            raise RuntimeError("ssh: connection reset by peer")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("ssh: connection reset by peer")
        name = re.search(r"wc -c < (\S+)", cmd).group(1)
        off = int(re.search(r"tail -c \+(\d+)", cmd).group(1))
        data = bytes(self.files[name])
        return f"{len(data)}\n" + data[off - 1:].decode("utf-8", "replace")

    def write(self, name: str, chunk: bytes):
        self.files[name] += chunk


def test_ssh_source_offsets_advance():
    files = ["jobs/abc/out/worker.log", "jobs/abc/log.txt"]
    remote = FakeRemote({"jobs/abc/out/worker.log": b"line1\n", "jobs/abc/log.txt": b"boot\n"})
    src = ssh_log_source(remote.run, files)
    chunk, size = src(0)
    assert chunk == b"line1\nboot\n" and size == len(b"line1\n") + len(b"boot\n")
    remote.write("jobs/abc/out/worker.log", b"line2\n")
    chunk, size2 = src(size)
    assert chunk == b"line2\n"  # only the new bytes, from the tracked offset
    assert size2 == len(b"line1\nline2\n") + len(b"boot\n")
    assert "tail -c +7" in remote.cmds[-2]  # worker.log call: per-file offset (6 bytes seen -> +7)


def test_ssh_source_keeps_offset_on_error_and_retries():
    remote = FakeRemote({"jobs/abc/out/worker.log": b"line1\n"})
    src = ssh_log_source(remote.run, ["jobs/abc/out/worker.log"])
    _, size = src(0)
    remote.write("jobs/abc/out/worker.log", b"line2\n")
    remote.failures = 1
    with pytest.raises(RuntimeError):
        src(size)  # transient ssh error propagates; the tailer keeps the last offset
    assert f"tail -c +{size + 1}" in remote.cmds[-1]  # the failed call used the kept offset
    chunk, size2 = src(size)  # retry with the same offset
    assert chunk == b"line2\n" and size2 == len(b"line1\nline2\n")
    assert f"tail -c +{size + 1}" in remote.cmds[-1]


def test_ssh_source_mid_tick_error_discards_whole_tick():
    """A mid-tick ssh error must not commit earlier files' offsets: the next tick
    re-reads from the last committed offsets, so no file's bytes are lost."""
    files = ["jobs/abc/out/worker.log", "jobs/abc/log.txt"]
    remote = FakeRemote({"jobs/abc/out/worker.log": b"one\n", "jobs/abc/log.txt": b"two\n"})
    remote.fail_sub = "jobs/abc/log.txt"  # the second file's read fails once
    src = ssh_log_source(remote.run, files)
    with pytest.raises(RuntimeError):
        src(0)  # worker.log read fine, log.txt raised: the tick is discarded
    assert len([c for c in remote.cmds if "worker.log" in c]) == 1
    chunk, size = src(0)  # offsets were not committed: both files re-read from byte 0
    assert chunk == b"one\ntwo\n" and size == len(b"one\n") + len(b"two\n")
    assert len([c for c in remote.cmds if "worker.log" in c]) == 2  # re-read, not lost
    assert "tail -c +1" in remote.cmds[-1]  # log.txt retried from its unchanged offset 0


def test_ssh_tailer_thread_appends_and_stops(lib):
    sid = lib.create("Tester", "Ssh Thread Song").id
    remote = FakeRemote({"jobs/abc/out/worker.log": b"start\n", "jobs/abc/log.txt": b""})
    tailer = LiveLogTailer(lib, sid, ssh_log_source(remote.run, ["jobs/abc/out/worker.log", "jobs/abc/log.txt"]),
                           interval=0.05).start()
    dest = lib.studio_dir(sid) / "worker.log"
    try:
        _wait_for(lambda: "start" in dest.read_text(encoding="utf-8"), what="first bytes")
        off1 = tailer.offset
        remote.write("jobs/abc/out/worker.log", b"more\n")
        _wait_for(lambda: tailer.offset > off1, what="offset advancing")
        _wait_for(lambda: "more" in dest.read_text(encoding="utf-8"), what="appended bytes")
        assert tailer.offset == len(b"start\nmore\n")
        content = dest.read_text(encoding="utf-8")
        assert content.count("start") == 1 and content.count("more") == 1  # no duplication
    finally:
        tailer.stop()
    assert tailer.thread is None or not tailer.thread.is_alive()


# ---------------------------------------------------------------- straggler tick after stop()
def test_tailer_straggler_tick_discards_bytes_after_stop(lib):
    """A tick can block in source() far beyond stop()'s join timeout (an ssh call hanging
    for minutes).  Its bytes must never land in worker.log after ingest()'s final copy."""
    sid = lib.create("Tester", "Straggler Song").id
    entered, release = threading.Event(), threading.Event()

    def source(_offset):
        entered.set()
        release.wait(timeout=10)  # the "hanging" tick, still blocked when stop() gives up
        return b"stale bytes\n", 42

    tailer = LiveLogTailer(lib, sid, source, interval=0.05).start()
    straggler = tailer.thread
    assert _wait_for(entered.is_set, what="the tick inside source()")
    tailer.stop()  # event set; join times out with the tick still blocked
    dest = lib.studio_dir(sid) / "worker.log"
    dest.write_bytes(b"FINAL authoritative log\n")  # what ingest() writes afterwards
    release.set()
    straggler.join(timeout=5)
    assert not straggler.is_alive()
    # the straggler tick discarded its bytes instead of appending after the final copy
    assert dest.read_bytes() == b"FINAL authoritative log\n"


def test_tailer_stale_tick_of_previous_run_discards_after_restart(lib):
    """After a stop() timed out on a blocked tick, start() begins a fresh run: the old
    run's ticks must not write into the new run's worker.log."""
    sid = lib.create("Tester", "Restart Song").id
    entered, release = threading.Event(), threading.Event()
    calls = []

    def source(_offset):
        calls.append(_offset)
        if len(calls) == 1:  # the tick of the first run blocks, like a hanging ssh call
            entered.set()
            release.wait(timeout=10)
            return b"stale bytes\n", 42
        return b"", 0

    tailer = LiveLogTailer(lib, sid, source, interval=0.05).start()
    old_thread = tailer.thread
    assert _wait_for(entered.is_set, what="the first run's tick inside source()")
    tailer.stop()  # join times out: the old tick is still blocked
    tailer.start()  # fresh run: new epoch, thread, fresh log — the new tick returns nothing
    release.set()  # the old run's straggler tick completes
    old_thread.join(timeout=5)
    assert not old_thread.is_alive()
    tailer.stop()
    # the old run's bytes never landed in the new run's fresh log
    assert (lib.studio_dir(sid) / "worker.log").read_bytes() == b""


def test_worker_log_endpoint_plain_and_after(studio):
    lib = studio.app.state.lib
    sid = lib.create("Tester", "Endpoint Song").id
    data = b"hello world, this is the log\n"
    (lib.studio_dir(sid) / "worker.log").write_bytes(data)

    r = studio.get(f"/api/songs/{sid}/worker-log")  # plain call: full text (backward compatible)
    assert r.status_code == 200
    assert r.text == data.decode()
    assert r.headers["x-total-size"] == str(len(data))

    r = studio.get(f"/api/songs/{sid}/worker-log?after=6")  # only the remainder + total size
    assert r.status_code == 200
    assert r.text == "world, this is the log\n"
    assert r.headers["x-total-size"] == str(len(data))

    r = studio.get(f"/api/songs/{sid}/worker-log?after=9999")
    assert r.text == "" and r.headers["x-total-size"] == str(len(data))

    sid2 = lib.create("Tester", "No Log Yet").id  # no log file at all
    r = studio.get(f"/api/songs/{sid2}/worker-log")
    assert r.status_code == 200 and r.text == "" and r.headers["x-total-size"] == "0"


def test_endpoint_serves_live_appends(studio, tmp_path):
    lib = studio.app.state.lib
    sid = lib.create("Tester", "Live Endpoint Song").id
    job = tmp_path / "job"
    (job / "out").mkdir(parents=True)
    src = job / "out" / "worker.log"
    src.write_bytes(b"part1\n")
    tailer = LiveLogTailer(lib, sid, local_log_source(job), interval=0.05).start()
    try:
        _wait_for(lambda: "part1" in studio.get(f"/api/songs/{sid}/worker-log").text, what="part1 served")
        total = int(studio.get(f"/api/songs/{sid}/worker-log").headers["x-total-size"])
        assert studio.get(f"/api/songs/{sid}/worker-log?after={total}").text == ""  # nothing new yet
        with open(src, "ab") as f:
            f.write(b"part2\n")
        _wait_for(lambda: int(studio.get(f"/api/songs/{sid}/worker-log").headers["x-total-size"]) > total,
                  what="the log growing")
        r = studio.get(f"/api/songs/{sid}/worker-log?after={total}")
        assert r.text == "part2\n" and int(r.headers["x-total-size"]) == total + len(b"part2\n")
    finally:
        tailer.stop()


# ---------------------------------------------------------------- end to end over a fake "ssh" server
# A pure-Python fake of SshTransport (no real ssh, no bash, no systemd): the fake worker
# runs in a thread and writes jobs/<id>/out/worker.log in steps while the job is running.
def _fake_worker(job: Path):
    out = job / "out"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "worker.log", "w", newline="\n") as f:
        f.write("job started\n")
    (job / "status.json").write_text(json.dumps({"state": "running", "stage": "working"}))
    for i in range(4):
        time.sleep(0.5)
        with open(out / "worker.log", "a", newline="\n") as f:
            f.write(f"step {i} done\n")
    (out / "editor.json").write_text("{}")
    (out / "vocals.ogg").write_bytes(b"OggS")
    (out / "song.txt").write_text("#TITLE:T\n#ARTIST:A\n#BPM:300\n#GAP:0\n: 0 4 0 La\nE\n", newline="\n")
    (out / "song.ultraai.json").write_text(json.dumps({"issues": [], "lyrics_source": "user"}))
    (job / "status.json").write_text(json.dumps({"state": "done", "stage": "finished"}))


class FakeServerTransport:
    """SshTransport's interface, implemented against a local home dir in pure Python."""

    def __init__(self, home: Path):
        self.home = Path(home)

    def run(self, cmd: str, timeout: float = 120) -> str:
        if cmd.startswith("START "):  # the (overridden) start_template
            job = self.home / "UltraAI" / "jobs" / cmd.split()[-1]
            threading.Thread(target=_fake_worker, args=(job,), daemon=True).start()
            return ""
        if "wc -c <" in cmd:  # the live-log tail command of ssh_log_source
            name = re.search(r"wc -c < (\S+)", cmd).group(1)
            off = int(re.search(r"tail -c \+(\d+)", cmd).group(1))
            p = self.home / name
            data = p.read_bytes() if p.exists() else b""
            return f"{len(data)}\n" + data[off - 1:].decode("utf-8", "replace")
        if cmd.startswith("mkdir -p "):
            (self.home / cmd.split()[-1]).mkdir(parents=True, exist_ok=True)
            return ""
        if cmd.startswith("rm -rf "):
            shutil.rmtree(self.home / cmd.split()[-1], ignore_errors=True)
            return ""
        raise RuntimeError(f"unexpected remote command: {cmd}")

    def stream(self, cmd: str):
        jd = self.home / re.search(r"job-status (\S+) --follow", cmd).group(1)
        last = None
        while True:
            try:
                st = (jd / "status.json").read_text(encoding="utf-8")
            except OSError:
                st = '{"state": "queued"}'
            if st != last:
                yield st
                last = st
            if json.loads(st)["state"] in ("done", "needs_lyrics", "error"):
                return
            time.sleep(0.05)

    def put(self, local: Path, remote: str) -> None:
        dst = self.home / remote
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dst)

    def get_dir(self, remote: str, local: Path) -> None:
        shutil.copytree(self.home / remote, Path(local) / Path(remote).name, dirs_exist_ok=True)


@pytest.fixture
def ssh_studio(tmp_path):
    """Studio whose "server" is the FakeServerTransport (no real ssh, no systemd)."""
    home = tmp_path / "server"
    (home / "UltraAI").mkdir(parents=True)
    settings = Settings(library=str(tmp_path / "lib"), processor="ssh", ssh_host="fake")

    def proc():
        p = SshProcessor(app.state.lib, FakeServerTransport(home))
        p.start_template = "START {id}"  # interpreted by FakeServerTransport.run
        p.live_log_interval = 0.1
        return p

    app = create_app(settings, processor_factory=proc, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


def _wait_state(client, sid, want, timeout=20):
    t = time.time() + timeout
    while time.time() < t:
        s = client.get(f"/api/songs/{sid}").json()
        if s["state"] in want:
            return s
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {want}, state={s['state']} error={s.get('error')}")


def test_ssh_job_log_streams_live_end_to_end(ssh_studio, tmp_path):
    client = ssh_studio
    f = tmp_path / "Some Artist - Some Song.wav"
    _wav(f)
    r = client.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))], headers=H)
    assert r.status_code == 200, r.text
    sid = r.json()[0]["id"]

    _wait_state(client, sid, ("processing",), timeout=10)
    # while the job is still running, the worker-log endpoint already streams content
    saw_live = None
    t = time.time() + 10
    while time.time() < t:
        s = client.get(f"/api/songs/{sid}").json()
        if s["state"] not in ("processing", "downloading", "queued"):
            break
        r = client.get(f"/api/songs/{sid}/worker-log")
        if "step" in r.text and int(r.headers["x-total-size"]) > 0:
            saw_live = r.text
            break
        time.sleep(0.05)
    assert saw_live, "no live log while the song was still processing"

    s = _wait_state(client, sid, ("needs_review", "error"))
    assert s["state"] == "needs_review", s
    final = client.get(f"/api/songs/{sid}/worker-log").text  # authoritative final copy
    assert "job started" in final and "step 3 done" in final
    assert "step" in saw_live  # served while the job was still running
