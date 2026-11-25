"""PR-17: yt-dlp --impersonate + --sleep-subtitles to lift YouTube 429s on subtitle fetches.

YouTube restricts auto-subtitles for sessions younger than ~60 s and rate-limits
plain clients; impersonating a browser TLS fingerprint (needs curl_cffi) plus a
60 s pre-subtitle sleep reliably fetches subs again (verified against live YouTube).

curl_cffi is a [worker] extra: when it is not installed the --impersonate flag is
dropped (with a one-time warning) instead of breaking every yt-dlp call — tests pin
``find_spec("curl_cffi")`` so they do not depend on the environment.

No real network: ytsubs._run is monkeypatched.  Privacy: only the cookies PATH is
ever passed on; contents never appear in commands or logs.
"""

import logging

import pytest

from ultraai import config, ytsubs

SAMPLE = {"events": [{"tStartMs": 1000, "dDurationMs": 1000,
                      "segs": [{"utf8": "Hello world", "tStartMs": 1000, "dDurationMs": 1000}]}]}


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ytsubs, "CACHE_DIR", tmp_path)
    return tmp_path


def _curl_cffi(monkeypatch, present: bool) -> None:
    """Pin curl_cffi (un)availability: ``find_spec("curl_cffi")`` is what the code
    checks; the [worker] extra may or may not be installed in the test env."""
    import importlib.util

    real = importlib.util.find_spec

    def fake(name, *args, **kw):
        if name == "curl_cffi":
            return object() if present else None
        return real(name, *args, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def _fake_run_factory(calls, files):
    """A fake _run: writes `files` (name -> dict) as json3 into the -o tmp dir."""
    import json
    import os

    def fake(cmd):
        calls.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        for name, data in files.items():
            with open(os.path.join(os.path.dirname(out), name), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data))
        return ""
    return fake


def test_options_registered_with_defaults(monkeypatch):
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    monkeypatch.delenv("ULTRAAI_YT_SLEEP_SUBS", raising=False)
    assert config.get("yt_impersonate") == "chrome"
    assert config.get("yt_sleep_subs") == "60"
    assert "ULTRAAI_YT_IMPERSONATE" in config.table()
    assert "ULTRAAI_YT_SLEEP_SUBS" in config.table()


def test_ytdlp_extra_defaults_include_impersonate_and_sleep_subs(monkeypatch):
    _curl_cffi(monkeypatch, present=True)
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    monkeypatch.delenv("ULTRAAI_YT_SLEEP_SUBS", raising=False)
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    args = ytsubs._ytdlp_extra()
    assert args[args.index("--impersonate") + 1] == "chrome"
    assert args[args.index("--sleep-subtitles") + 1] == "60"


def test_ytdlp_extra_impersonate_dropped_without_curl_cffi(monkeypatch, caplog):
    """A Studio-only install has no curl_cffi ([worker] extra): the flag is dropped
    and a warning logged once, instead of yt-dlp failing on every call."""
    _curl_cffi(monkeypatch, present=False)
    monkeypatch.setattr(ytsubs, "_impersonate_warned", False)
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    monkeypatch.setenv("ULTRAAI_YT_SLEEP_SUBS", "0")
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    with caplog.at_level(logging.WARNING, logger="ultraai.ytsubs"):
        assert ytsubs._ytdlp_extra() == []
        ytsubs._ytdlp_extra()
    warns = [r for r in caplog.records if "curl_cffi" in r.message]
    assert len(warns) == 1  # warned once per process, not per call


def test_impersonation_failure_warns_before_raising(cache_dir, monkeypatch, caplog):
    """A non-429 impersonate failure re-raises (callers turn it into None/[]) but
    is warned about first: without the hint the swallowed failure is silent."""
    def boom(cmd):
        raise RuntimeError("yt-dlp failed (rc=1): "
                           "ERROR: Impersonate target chrome is not available")

    monkeypatch.setattr(ytsubs, "_run", boom)
    _curl_cffi(monkeypatch, present=True)
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    with caplog.at_level(logging.WARNING, logger="ultraai.ytsubs"):
        assert ytsubs.fetch_subs("imp3") is None
    assert any("failed on --impersonate" in r.message for r in caplog.records)


def test_ytdlp_extra_disabled(monkeypatch):
    monkeypatch.setenv("ULTRAAI_YT_IMPERSONATE", "")
    monkeypatch.setenv("ULTRAAI_YT_SLEEP_SUBS", "0")
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    assert ytsubs._ytdlp_extra() == []


@pytest.mark.parametrize("bad", ["not-a-number", "inf", "nan", "-5", "2e6"])
def test_ytdlp_extra_bad_sleep_value_ignored(monkeypatch, bad):
    """Unparseable, non-finite and out-of-range values all behave the same: no flag."""
    _curl_cffi(monkeypatch, present=True)
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    monkeypatch.setenv("ULTRAAI_YT_SLEEP_SUBS", bad)
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    args = ytsubs._ytdlp_extra()
    assert "--sleep-subtitles" not in args
    assert "--impersonate" in args


def test_fetch_subs_command_carries_flags(cache_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {"sub.en.json3": SAMPLE}))
    _curl_cffi(monkeypatch, present=True)
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    monkeypatch.delenv("ULTRAAI_YT_SLEEP_SUBS", raising=False)
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    assert ytsubs.fetch_subs("imp1") == SAMPLE
    cmd = calls[0]
    assert cmd[cmd.index("--impersonate") + 1] == "chrome"
    assert cmd[cmd.index("--sleep-subtitles") + 1] == "60"


def test_resolve_ytid_command_carries_flags(cache_dir, monkeypatch):
    calls = []

    def fake_print(cmd):
        calls.append(cmd)
        return "abc123XYZ\n"

    monkeypatch.setattr(ytsubs, "_run", fake_print)
    _curl_cffi(monkeypatch, present=True)
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    assert ytsubs.resolve_ytid("Some Artist - Some Title") == "abc123XYZ"
    cmd = calls[0]
    assert "--impersonate" in cmd and "--sleep-subtitles" in cmd


def test_cookies_path_only_never_contents(cache_dir, tmp_path, monkeypatch):
    marker = "FAKE_COOKIE_SECRET_MARKER"
    f = tmp_path / "cookies.txt"
    f.write_text(f"# Netscape HTTP Cookie File\n.youtube.com\tFAKE\t{marker}\n", encoding="utf-8")
    monkeypatch.setenv("ULTRAAI_COOKIES", str(f))
    calls = []
    monkeypatch.setattr(ytsubs, "_run", _fake_run_factory(calls, {"sub.en.json3": SAMPLE}))
    ytsubs.fetch_subs("imp2")
    cmd = calls[0]
    assert cmd[cmd.index("--cookies") + 1] == str(f)
    assert marker not in str(cmd)


def test_studio_importer_opts_include_impersonate_and_cookiefile(tmp_path, monkeypatch):
    from ultraai.studio import importer

    f = tmp_path / "cookies.txt"
    f.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setenv("ULTRAAI_COOKIES", str(f))
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    _curl_cffi(monkeypatch, present=True)
    opts = importer._cookie_opts()
    assert opts["cookiefile"] == str(f)
    assert opts["impersonate"] == "chrome"

    monkeypatch.setenv("ULTRAAI_YT_IMPERSONATE", "")
    monkeypatch.delenv("ULTRAAI_COOKIES", raising=False)
    assert importer._cookie_opts() == {}


def test_studio_importer_impersonate_dropped_without_curl_cffi(tmp_path, monkeypatch, caplog):
    from ultraai.studio import importer

    f = tmp_path / "cookies.txt"
    f.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setenv("ULTRAAI_COOKIES", str(f))
    monkeypatch.delenv("ULTRAAI_YT_IMPERSONATE", raising=False)
    _curl_cffi(monkeypatch, present=False)
    monkeypatch.setattr(importer, "_impersonate_warned", False)
    with caplog.at_level(logging.WARNING, logger="ultraai.import"):
        assert importer._cookie_opts() == {"cookiefile": str(f)}
        importer._cookie_opts()
    warns = [r for r in caplog.records if "curl_cffi" in r.message]
    assert len(warns) == 1  # warned once per process, not per call
