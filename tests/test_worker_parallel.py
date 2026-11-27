"""Sharded worker locks, ULTRAAI_MAX_JOBS, crash-safe background pause, atomic stage_rates.

Threading + fakes only: no real audio, no real flock (fcntl is POSIX-only; the worker runs on
the Linux server, the tests run anywhere).  ``worker._acquire``/``worker._release`` are faked
with in-process mutexes keyed by lock-file path, so the real ``_Lock``/``_Slots``/``_job_locks``
logic is exercised.
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from ultraai import config, worker


class FakeFlock:
    """In-process replacement for worker._acquire/_release, keyed by lock file path."""

    def __init__(self):
        self.cond = threading.Condition()
        self.held: dict[str, int] = {}   # resolved lock path -> owning thread id
        self.acquired: list[str] = []    # every successful acquisition, in order

    @staticmethod
    def _key(f) -> str:
        return str(Path(f.name).resolve())

    def acquire(self, f, blocking: bool = True) -> bool:
        key = self._key(f)
        with self.cond:
            while key in self.held:
                if not blocking:
                    return False
                self.cond.wait(0.05)
            self.held[key] = threading.get_ident()
            self.acquired.append(key)
            return True

    def release(self, f) -> None:
        with self.cond:
            self.held.pop(self._key(f), None)
            self.cond.notify_all()


@pytest.fixture
def fake_flock(monkeypatch):
    fake = FakeFlock()
    monkeypatch.setattr(worker, "_acquire", fake.acquire)
    monkeypatch.setattr(worker, "_release", fake.release)
    return fake


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the worker's lock/rate/pause files at a tmp cache dir."""
    monkeypatch.setattr(worker, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(worker, "LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(worker, "SLOTS_DIR", tmp_path / "locks" / "slots")
    monkeypatch.setattr(worker, "GLOBAL_LOCK", tmp_path / "worker.lock")
    monkeypatch.setattr(worker, "RATES_FILE", tmp_path / "stage_rates.json")
    monkeypatch.setattr(worker, "PAUSED_FILE", tmp_path / "background.paused")
    monkeypatch.setattr(worker, "PRESENCE_DIR", tmp_path / "locks" / "background")
    return tmp_path


def _make_job(root: Path, name: str, audio_bytes: bytes) -> Path:
    job = root / name
    job.mkdir()
    (job / "a.m4a").write_bytes(audio_bytes)
    (job / "job.json").write_text(json.dumps({"id": name, "audio": "a.m4a"}), encoding="utf-8")
    return job


def _spec(job: Path) -> dict:
    return json.loads((job / "job.json").read_text(encoding="utf-8"))


class _Threads:
    """Run callables on threads, join them, and re-raise any worker exception in the main one."""

    def __init__(self, *targets):
        self.errors: list[BaseException] = []
        self.ts = []
        for t in targets:
            def run(fn=t):
                try:
                    fn()
                except BaseException as exc:  # noqa: BLE001 - surfaced below
                    self.errors.append(exc)
            th = threading.Thread(target=run)
            self.ts.append(th)

    def start(self):
        for t in self.ts:
            t.start()
        return self

    def join(self, timeout=10):
        for t in self.ts:
            t.join(timeout)
        assert not any(t.is_alive() for t in self.ts), "a worker thread deadlocked"
        assert not self.errors, f"worker thread raised: {self.errors[0]!r}"
        return self


def _run(*targets, timeout=10):
    return _Threads(*targets).start().join(timeout)


class _Concurrency:
    def __init__(self):
        self.g = threading.Lock()
        self.inside = 0
        self.max = 0

    def enter(self):
        with self.g:
            self.inside += 1
            self.max = max(self.max, self.inside)

    def exit(self):
        with self.g:
            self.inside -= 1


# ------------------------------------------------------------------ option


def test_max_jobs_option_registered(monkeypatch):
    assert config.get("max_jobs") == "1"
    assert config.env_of("max_jobs") == "ULTRAAI_MAX_JOBS"
    assert worker.max_jobs() == 1                      # default: today's behavior
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "3")
    assert worker.max_jobs() == 3
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "0")
    assert worker.max_jobs() == 1                      # clamped
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "garbage")
    assert worker.max_jobs() == 1                      # unreadable -> 1


# ------------------------------------------------------------------ sharded locks


def test_different_hashes_hold_their_locks_concurrently(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "4")
    inside = threading.Semaphore(0)
    proceed = threading.Event()

    def run(name, audio):
        job = _make_job(cache, name, audio)
        with worker._job_locks(job, _spec(job)):
            inside.release()
            assert proceed.wait(5)

    th = _Threads(lambda: run("j0", b"\x00" * 256), lambda: run("j1", b"\x01" * 256)).start()
    assert inside.acquire(timeout=5) and inside.acquire(timeout=5)  # both inside at once
    proceed.set()
    th.join()
    locks = sorted(p.name for p in (cache / "locks").glob("*.lock"))
    assert len(locks) == 2 and all(name.endswith(".lock") for name in locks)


def test_same_hash_serializes(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "4")
    conc = _Concurrency()

    def run(name):
        job = _make_job(cache, name, b"identical-audio-bytes")
        with worker._job_locks(job, _spec(job)):
            conc.enter()
            time.sleep(0.25)
            conc.exit()

    _run(lambda: run("j0"), lambda: run("j1"))
    assert conc.max == 1  # same audio hash: one at a time, even with max_jobs=4
    assert len(list((cache / "locks").glob("*.lock"))) == 1  # one shared per-hash lock file


def test_max_jobs_bounds_concurrency(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "2")
    conc = _Concurrency()
    entered = [threading.Event() for _ in range(3)]
    proceed = threading.Event()

    def run(i):
        job = _make_job(cache, f"j{i}", bytes([i]) * 128)
        with worker._job_locks(job, _spec(job)):
            conc.enter()
            entered[i].set()
            assert proceed.wait(5)
            conc.exit()

    th = _Threads(*[lambda i=i: run(i) for i in range(3)]).start()
    deadline = time.time() + 5
    while sum(e.is_set() for e in entered) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert sum(e.is_set() for e in entered) == 2   # 2 distinct hashes inside...
    time.sleep(0.2)
    assert sum(e.is_set() for e in entered) == 2   # ...the 3rd still waits
    proceed.set()
    th.join()
    assert conc.max == 2
    assert len(list((cache / "locks" / "slots").glob("slot-*.lock"))) == 2


def test_lock_raises_when_flock_fails(cache, monkeypatch):
    """A job must never proceed without its lock (flock can fail, e.g. on NFS)."""
    monkeypatch.setattr(worker, "_acquire", lambda f, blocking=True: False)
    monkeypatch.setattr(worker, "_release", lambda f: None)
    with pytest.raises(OSError):
        with worker._Lock(cache / "worker.lock"):
            raise AssertionError("proceeded without the lock")


def test_max_jobs_1_keeps_the_single_global_lock(fake_flock, cache, monkeypatch):
    monkeypatch.delenv("ULTRAAI_MAX_JOBS", raising=False)
    conc = _Concurrency()

    def run(name, audio):
        job = _make_job(cache, name, audio)
        with worker._job_locks(job, _spec(job)):
            conc.enter()
            time.sleep(0.15)
            conc.exit()

    _run(lambda: run("j0", b"\x00" * 64), lambda: run("j1", b"\x01" * 64))
    # byte-identical legacy path: only CACHE_DIR/worker.lock, no locks/ dir, everything serialized
    assert set(fake_flock.acquired) == {str((cache / "worker.lock").resolve())}
    assert not (cache / "locks").exists()
    assert conc.max == 1


# ------------------------------------------------------------------ process() wiring


def _fake_pipeline(monkeypatch, conc, seconds=0.3):
    def fake_process(job, spec, out):
        conc.enter()
        time.sleep(seconds)
        conc.exit()
        worker.write_status(job, "done")
        return {"state": "done", "issues": 0}

    monkeypatch.setattr(worker, "_process", fake_process)


def test_process_runs_distinct_hashes_in_parallel(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "2")
    monkeypatch.setattr(worker, "BACKGROUND_UNITS", [])
    conc = _Concurrency()
    _fake_pipeline(monkeypatch, conc)
    jobs = [_make_job(cache, f"j{i}", bytes([i]) * 512) for i in range(3)]
    _run(*[lambda j=j: worker.process(j) for j in jobs])
    assert conc.max == 2
    for j in jobs:
        assert json.loads((j / "status.json").read_text(encoding="utf-8"))["state"] == "done"
    assert len(list((cache / "locks").glob("*.lock"))) == 3
    assert not (cache / "background.paused").exists()  # counter cleaned up on exit


def test_process_same_audio_serializes(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "2")
    monkeypatch.setattr(worker, "BACKGROUND_UNITS", [])
    conc = _Concurrency()
    _fake_pipeline(monkeypatch, conc)
    jobs = [_make_job(cache, f"j{i}", b"same-bytes" * 64) for i in range(2)]
    _run(*[lambda j=j: worker.process(j) for j in jobs])
    assert conc.max == 1


# ------------------------------------------------------------------ crash-safe pause/resume


class _Freezer:
    """Fake systemd: units have a FreezerState, so freezing is idempotent across jobs,
    like the real systemctl pause units rely on."""

    def __init__(self, units, active):
        self.units = units
        self.active = set(active)
        self.frozen: set[str] = set()
        self.calls: list[tuple] = []

    def systemctl(self, *args):
        self.calls.append(args)
        if args[0] == "show":
            if args[1] not in self.active:
                return "inactive dead"
            return f"active {'frozen' if args[1] in self.frozen else 'running'}"
        if args[0] == "freeze":
            self.frozen.add(args[1])
        elif args[0] == "thaw":
            self.frozen.discard(args[1])
        return ""

    def calls_of(self, verb):
        return [c for c in self.calls if c[0] == verb]


def test_background_pause_no_thaw_while_any_job_inside(fake_flock, cache, monkeypatch):
    """Two guards overlapping: one freeze while both are inside, and the first guard to
    exit must neither thaw (background gives way while *any* song runs) nor lose the
    frozen-units record; only the last exit thaws (0->0 presence)."""
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "4")
    monkeypatch.setattr(worker, "BACKGROUND_UNITS", ["u1.service", "u2.service"])
    fz = _Freezer(["u1.service", "u2.service"], active=["u1.service"])
    monkeypatch.setattr(worker, "_systemctl", fz.systemctl)
    inside = threading.Semaphore(0)
    finish = [threading.Event() for _ in range(2)]
    left = [threading.Event() for _ in range(2)]

    def guard(i):
        with worker._background_guard():
            inside.release()
            assert finish[i].wait(5)
        left[i].set()  # outside the guard: any thaw from here on is a regression

    th = _Threads(lambda: guard(0), lambda: guard(1)).start()
    assert inside.acquire(timeout=5) and inside.acquire(timeout=5)   # both inside
    assert fz.calls_of("freeze") == [("freeze", "u1.service")]        # u2 not active
    assert fz.frozen == {"u1.service"}                               # paused on the first entry
    finish[0].set()                                                  # guard 0 leaves first...
    assert left[0].wait(5)
    assert fz.calls_of("thaw") == []                                 # ...but must NOT thaw
    assert json.loads((cache / "background.paused").read_text(encoding="utf-8"))["units"] == ["u1.service"]
    finish[1].set()
    th.join()
    assert fz.calls_of("thaw") == [("thaw", "u1.service")]            # thawed once, on the last exit
    assert fz.frozen == set()
    assert not (cache / "background.paused").exists()                 # record removed with the thaw
    assert list((cache / "locks" / "background").glob("*.lock")) == []  # presence files cleaned up


def test_background_guard_survives_killed_worker(fake_flock, cache, monkeypatch):
    """Studio cancel kills the worker (proc.kill: no finally).  A job's presence is the
    flock on its own file — the OS releases that when the process dies — so the stale
    presence file of a killed job must not stop later guards from thawing: no stuck
    refcount, background units freeze/thaw normally afterwards."""
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "4")
    monkeypatch.setattr(worker, "BACKGROUND_UNITS", ["u1.service"])
    fz = _Freezer(["u1.service"], active=["u1.service"])
    monkeypatch.setattr(worker, "_systemctl", fz.systemctl)
    # what the cache looks like after a kill: the dead job's presence file sits there with
    # no flock on it, and the unit it froze is still in the record (nobody ran finally)
    pdir = cache / "locks" / "background"
    pdir.mkdir(parents=True)
    (pdir / "job-999999-0.lock").touch()
    worker._write_paused(["u1.service"])
    fz.frozen.add("u1.service")

    def guard():
        with worker._background_guard():
            assert fz.frozen == {"u1.service"}   # still frozen while a job runs

    _run(guard)
    assert "u1.service" not in fz.frozen         # last exit thawed, dead job notwithstanding
    assert fz.calls_of("thaw") == [("thaw", "u1.service")]
    assert not (cache / "background.paused").exists()   # record cleaned up: no leak
    assert list(pdir.glob("*.lock")) == []               # the dead job's file was removed


def test_background_guard_max_jobs_1_is_legacy(fake_flock, cache, monkeypatch):
    monkeypatch.delenv("ULTRAAI_MAX_JOBS", raising=False)
    monkeypatch.setattr(worker, "BACKGROUND_UNITS", ["u1.service"])
    calls = []
    monkeypatch.setattr(worker, "_systemctl", lambda *a: calls.append(a) or "active running")
    with worker._background_guard():
        assert calls.count(("freeze", "u1.service")) == 1
        assert not (cache / "background.paused").exists()  # no counter file on the legacy path
        assert fake_flock.acquired == []                    # no extra lock taken
    assert calls.count(("thaw", "u1.service")) == 1


# ------------------------------------------------------------------ atomic stage_rates.json


def test_save_rates_atomic_merge_under_global_lock(fake_flock, cache, monkeypatch):
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "2")
    worker.RATES_FILE.write_text(json.dumps({"lyrics": 0.01}), encoding="utf-8")
    worker._save_rates({"analyse": 0.42})
    assert json.loads(worker.RATES_FILE.read_text(encoding="utf-8")) == {"lyrics": 0.01, "analyse": 0.42}
    assert not (cache / "stage_rates.json.tmp").exists()                      # tmp+replace
    assert str((cache / "worker.lock").resolve()) in fake_flock.acquired       # under the global lock

    def w(i):
        for _ in range(25):
            worker._save_rates({f"k{i}": float(i)})

    _run(*[lambda i=i: w(i) for i in range(4)])
    data = json.loads(worker.RATES_FILE.read_text(encoding="utf-8"))
    assert data["lyrics"] == 0.01                                             # nothing clobbered
    assert all(data[f"k{i}"] == float(i) for i in range(4))
    assert not (cache / "stage_rates.json.tmp").exists()


def test_save_rates_max_jobs_1_writes_whole_dict_without_extra_lock(fake_flock, cache, monkeypatch):
    monkeypatch.delenv("ULTRAAI_MAX_JOBS", raising=False)
    worker.RATES_FILE.write_text(json.dumps({"lyrics": 0.01}), encoding="utf-8")
    worker._save_rates({"analyse": 0.42})  # as before: the whole in-memory dict, no merge
    assert json.loads(worker.RATES_FILE.read_text(encoding="utf-8")) == {"analyse": 0.42}
    assert str((cache / "worker.lock").resolve()) not in fake_flock.acquired  # job already holds it
    assert not (cache / "stage_rates.json.tmp").exists()


# ------------------------------------------------------------------ OMP_NUM_THREADS


def test_subprocess_env_omp_threads(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "3")
    assert worker.subprocess_env()["OMP_NUM_THREADS"] == "2"   # cores // max_jobs
    assert "OMP_NUM_THREADS" not in os.environ                   # os.environ never mutated
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "8")
    assert worker.subprocess_env()["OMP_NUM_THREADS"] == "1"    # clamped at 1
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "1")
    assert "OMP_NUM_THREADS" not in worker.subprocess_env()     # legacy: nothing added
    monkeypatch.setenv("OMP_NUM_THREADS", "7")
    monkeypatch.setenv("ULTRAAI_MAX_JOBS", "2")
    assert worker.subprocess_env()["OMP_NUM_THREADS"] == "7"    # explicit setting respected
