"""PR-16: ULTRAAI_COOKIES (Netscape cookies.txt path) + 429-resilient yt-dlp calls.

Privacy: cookie CONTENTS are secrets.  Tests only ever use a tmp fake file (written
here, never a real cookies file) and assert the fake contents never reach the logs.
Only the PATH is passed to yt-dlp via --cookies.

No real network: ytsubs._run and ytsubs._sleep are monkeypatched.
"""

import json
import logging
import os

import pytest

from ultraai import config, ytsubs

FAKE_COOKIE_MARKER = "FAKE_COOKIE_SECRET_MARKER"  # contents of the tmp fake file; must never be logged


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ytsubs, "CACHE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def cookies_file(tmp_path, monkeypatch):
    """A tmp fake cookies.txt + ULTRAAI_COOKIES pointing at it."""
    f = tmp_path / "cookies.txt"
    f.write_text(f"# Netscape HTTP Cookie File\n.youtube.com\tFAKE\t{FAKE_COOKIE_MARKER}\n", encoding="utf-8")
    monkeypatch.setenv("ULTRAAI_COOKIES", str(f))
    return f


@pytest.fixture
def sleeps(monkeypatch):
    got = []
    monkeypatch.setattr(ytsubs, "_sleep", got.append)
    return got


def _seg(text, t_ms=0, d_ms=100):
    return {"utf8": text, "tStartMs": t_ms, "dDurationMs": d_ms}


SAMPLE = {"events": [{"tStartMs": 1000, "dDurationMs": 1000, "segs": [_seg("Hello world", 1000, 1000)]}]}


def _fake_run_factory(calls, files):
    """A fake _run: writes `files` (name -> dict) as json3 into the -o tmp dir."""
    def fake(cmd):
        calls.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        for name, data in files.items():
            with open(os.path.join(os.path.dirname(out), name), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data))
        return ""
    return fake


# ---------------------------------------------------------------- config

def test_cookies_option_registered(monkeypatch):
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    assert config.get("cookies") == ""
    assert config.env_of("cookies") == "ULTRAAI_COOKIES"
    assert "ULTRAAI_COOKIES" in config.table()


# ---------------------------------------------------------------- --cookies flag

def test_cookies_flag_present_when_set_and_file_exists(cache_dir, cookies_file, monkeypatch):
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {"sub.en.json3": SAMPLE}))
    assert ytsubs.fetch_subs("ck1") == SAMPLE
    cmd = calls[0]
    assert cmd[cmd.index("--cookies") + 1] == str(cookies_file)
    assert cmd[cmd.index("--sleep-requests") + 1] == "1"

    calls.clear()

    def fake_print(cmd):
        calls.append(cmd)
        return "abc123XYZ\n"

    monkeypatch.setattr(ytsubs, "_run", fake_print)
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    cmd = calls[0]
    assert cmd[cmd.index("--cookies") + 1] == str(cookies_file)
    assert cmd[cmd.index("--sleep-requests") + 1] == "1"


def test_cookies_flag_absent_when_unset_or_missing(cache_dir, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {"sub.en.json3": SAMPLE}))
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    ytsubs.fetch_subs("ck2")
    assert "--cookies" not in calls[0]

    monkeypatch.setenv("ULTRAAI_COOKIES", str(tmp_path / "does-not-exist.txt"))
    ytsubs.fetch_subs("ck3")
    assert "--cookies" not in calls[1]
    # --sleep-requests 1 is always there
    assert calls[0][calls[0].index("--sleep-requests") + 1] == "1"


# ---------------------------------------------------------------- 429 resilience

def _boom_429(cmd):
    raise RuntimeError("yt-dlp failed (rc=1): ERROR: HTTP Error 429: Too Many Requests")


def test_429_in_stderr_retries_then_none_and_warns(cache_dir, cookies_file, monkeypatch, sleeps, caplog):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        _boom_429(cmd)

    monkeypatch.setattr(ytsubs, "_run", fake)
    with caplog.at_level(logging.WARNING, logger="ultraai.ytsubs"):
        assert ytsubs.fetch_subs("ck4") is None  # no exception
    assert len(calls) == 3                      # max 3 attempts
    assert len(sleeps) == 2                     # backoff before attempts 2 and 3
    assert 5.0 <= sleeps[0] < 5.0 + 60.0
    assert 20.0 <= sleeps[1] < 20.0 + 60.0
    assert any("rate-limited (429)" in r.message and "ULTRAAI_COOKIES" in r.message
               for r in caplog.records)
    assert FAKE_COOKIE_MARKER not in caplog.text  # cookie contents never logged


def test_429_in_stdout_also_retried(cache_dir, monkeypatch, sleeps):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return "ERROR: HTTP Error 429: Too Many Requests\n"

    monkeypatch.setattr(ytsubs, "_run", fake)
    assert ytsubs.resolve_ytid("x - y") is None
    assert len(calls) == 3 and len(sleeps) == 2


def test_success_after_one_429_retry_returns_data(cache_dir, cookies_file, monkeypatch, sleeps):
    calls = []
    state = {"n": 0}

    def fake(cmd):
        calls.append(cmd)
        state["n"] += 1
        if state["n"] == 1:
            _boom_429(cmd)
        out = cmd[cmd.index("-o") + 1]
        with open(os.path.join(os.path.dirname(out), "sub.en.json3"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(SAMPLE))
        return ""

    monkeypatch.setattr(ytsubs, "_run", fake)
    assert ytsubs.fetch_subs("ck5") == SAMPLE
    assert len(calls) == 2 and len(sleeps) == 1 and 5.0 <= sleeps[0] < 5.0 + 60.0


def test_non_429_failure_does_not_retry(cache_dir, monkeypatch, sleeps):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        raise RuntimeError("yt-dlp failed (rc=1): no network")

    monkeypatch.setattr(ytsubs, "_run", fake)
    assert ytsubs.fetch_subs("ck6") is None
    assert len(calls) == 1 and sleeps == []


def test_resolve_ytid_caches_with_cookies(cache_dir, cookies_file, monkeypatch):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return "abc123XYZ\n"

    monkeypatch.setattr(ytsubs, "_run", fake)
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    assert "--cookies" in calls[0]
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    assert len(calls) == 1  # cached: no second yt-dlp run


# ---------------------------------------------------------------- worker env

def test_worker_forwards_cookies_path_into_job_env(monkeypatch, tmp_path):
    from ultraai import worker

    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    worker.apply_options({"options": {"cookies": str(tmp_path / "c.txt")}})
    assert os.environ["ULTRAAI_COOKIES"] == str(tmp_path / "c.txt")

    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    worker.apply_options({"cookies": str(tmp_path / "top.txt")})
    assert os.environ["ULTRAAI_COOKIES"] == str(tmp_path / "top.txt")

    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    worker.apply_options({})  # not set in the job: a global env value is left alone
    assert "ULTRAAI_COOKIES" not in os.environ


# ---------------------------------------------------------------- studio settings API

H = {"X-UltraAI": "1"}


@pytest.fixture
def studio_client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("APPDATA", str(tmp_path))               # keep studio.json in tmp
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from fastapi.testclient import TestClient

    from ultraai.studio.app import create_app
    from ultraai.studio.config import Settings

    settings = Settings(library=str(tmp_path / "lib"), processor="local")
    app = create_app(settings, processor_factory=lambda: None, start_queue=False)
    with TestClient(app) as client:
        yield client, settings


def test_settings_put_rejects_nonexistent_cookies_path_with_400(studio_client, tmp_path):
    client, settings = studio_client
    r = client.put("/api/settings", json={"cookies": str(tmp_path / "nope.txt")}, headers=H)
    assert r.status_code == 400
    assert settings.cookies == ""  # unchanged


def test_settings_put_accepts_existing_file_and_get_returns_path(studio_client, tmp_path):
    client, settings = studio_client
    f = tmp_path / "cookies.txt"
    f.write_text(f"# fake {FAKE_COOKIE_MARKER}\n", encoding="utf-8")
    r = client.put("/api/settings", json={"cookies": str(f)}, headers=H)
    assert r.status_code == 200, r.text
    assert settings.cookies == str(f)
    got = client.get("/api/settings").json()
    assert got["cookies"] == str(f)  # a path is not secret
    # clearing works too
    r = client.put("/api/settings", json={"cookies": ""}, headers=H)
    assert r.status_code == 200 and settings.cookies == ""
