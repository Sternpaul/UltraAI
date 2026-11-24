"""lrc.py: single cache naming scheme, legacy migration, TTL and request politeness."""

import io
import json
import time
import urllib.request

import pytest

from ultraai import lrc


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lrc, "CACHE_DIR", tmp_path)
    d = tmp_path / "lrclib"
    d.mkdir(parents=True)
    return d


def _fake_urlopen(payload):
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return io.BytesIO(json.dumps(payload).encode())

    return fake, calls


def _no_http(req, timeout=None):
    raise AssertionError("urlopen called although the cache should have been used")


def test_default_ttl_is_30_days():
    assert lrc.CACHE_TTL_S == 30 * 24 * 3600


def test_legacy_cache_file_is_migrated_once(cache_dir, monkeypatch):
    params = {"artist_name": "Artist", "track_name": "Title"}
    legacy = cache_dir / lrc._legacy_cache_name(params)
    assert legacy.name == "Artist_Title.json"
    legacy.write_text(json.dumps([{"id": 1, "syncedLyrics": "[00:01.00] hi"}]))
    monkeypatch.setattr(urllib.request, "urlopen", _no_http)
    res = lrc._query(params)
    assert res[0]["id"] == 1
    # renamed to the new scheme, and only the new scheme remains on disk
    assert not legacy.exists()
    assert (cache_dir / lrc._cache_name(params)).exists()
    assert len(list(cache_dir.glob("*.json"))) == 1
    assert lrc._cache_name(params).startswith("q_")
    # second call: served from the migrated cache, still no HTTP
    assert lrc._query(params)[0]["id"] == 1


def test_fresh_cache_is_used_without_http(cache_dir, monkeypatch):
    params = {"track_name": "Song"}
    (cache_dir / lrc._cache_name(params)).write_text(json.dumps([{"id": 7}]))
    monkeypatch.setattr(urllib.request, "urlopen", _no_http)
    assert lrc._query(params) == [{"id": 7}]


def test_cache_ttl_expiry_refetches(cache_dir, monkeypatch):
    params = {"track_name": "Song"}
    cache = cache_dir / lrc._cache_name(params)
    cache.write_text(json.dumps([{"id": 1}]))
    fake, calls = _fake_urlopen([{"id": 2}])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    assert lrc._query(params) == [{"id": 1}]          # fresh: no HTTP
    assert calls == []
    monkeypatch.setattr(time, "time", lambda: now + lrc.CACHE_TTL_S + 1)
    assert lrc._query(params) == [{"id": 2}]          # expired: refetched
    assert len(calls) == 1
    assert json.loads(cache.read_text()) == [{"id": 2}]  # cache rewritten


def test_politeness_delay_between_http_requests(cache_dir, monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    t = [1000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])
    monkeypatch.setattr(lrc, "_last_request", t[0])  # a request just happened
    fake, calls = _fake_urlopen([{"id": 1}])
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    lrc._query({"track_name": "A"})
    assert sleeps == [pytest.approx(lrc.MIN_REQUEST_INTERVAL_S)]  # waited the full delay
    assert lrc.MIN_REQUEST_INTERVAL_S == 0.2
    t[0] += lrc.MIN_REQUEST_INTERVAL_S + 0.1
    lrc._query({"track_name": "B"})  # enough time passed: no extra delay
    assert len(sleeps) == 1
    assert len(calls) == 2
