"""Lyrics supplied at add time (before processing) land in .studio/lyrics.txt and in the
queued job spec — POST /api/songs/url (json field ``lyrics``) and POST /api/songs/files
(form field ``lyrics``).  Everything runs on a tiny fake library in tmp_path: no songs/
folder, no network (the YouTube fetch is monkeypatched), no SSH."""

import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import ultraai.studio.importer as importer  # noqa: E402
from ultraai.studio.app import MAX_LYRICS, create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import Processor  # noqa: E402

PORT = 8766
TOKEN = "test-token-lyrics-add-0123456789"
BASE = f"http://127.0.0.1:{PORT}"
H = {"x-ultraai": TOKEN}
URL = "https://www.youtube.com/watch?v=abc12345678"


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
    app = create_app(settings, processor_factory=lambda: None,
                     start_queue=False, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


@pytest.fixture
def fake_youtube(monkeypatch):
    """import_youtube without network: info + a fake audio download, no video/cover."""
    monkeypatch.setattr(importer, "youtube_info",
                        lambda url, cookies=None: {"artist": "Fake Artist", "track": "Fake Title",
                                                   "title": "Fake Title", "id": "abc12345678"})

    def fake_download(url, fmt, dest, hook=None, cookies=None):
        p = dest / "dl.m4a"
        p.write_bytes(b"RIFF" + b"\0" * 60)
        return p

    monkeypatch.setattr(importer, "_download", fake_download)
    monkeypatch.setattr(importer, "fetch_extras", lambda *a, **kw: [])


def _wav(path: Path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)


def _upload(client, tmp_path, lyrics=None, name="Some Artist - Some Song.wav"):
    f = tmp_path / name
    _wav(f)
    data = {} if lyrics is None else {"lyrics": lyrics}
    r = client.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))],
                    data=data, headers=H)
    assert r.status_code == 200, r.text
    return r.json()[0]["id"]


def _add_url(client, lyrics=None):
    body = {"url": URL}
    if lyrics is not None:
        body["lyrics"] = lyrics
    r = client.post("/api/songs/url", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_song(client, timeout=10):
    """The URL add runs in a background thread; wait until the song is created and queued
    (audio is in place by then, so the job spec can be built)."""
    t = time.time() + timeout
    items = []
    while time.time() < t:
        items = client.get("/api/songs").json()
        if items and items[0]["state"] in ("queued", "error"):
            return items[0]["id"]
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for the song to be created: {items}")


def _lyrics_file(client, sid) -> Path:
    return client.app.state.lib.studio_dir(sid) / "lyrics.txt"


def _spec(client, sid) -> dict:
    return Processor(client.app.state.lib).job_spec(sid, "testjob")


# ---------------------------------------------------------------- url add
def test_url_with_lyrics(studio, fake_youtube):
    _add_url(studio, lyrics="  my own\nlyrics here  \n")
    sid = _wait_song(studio)
    p = _lyrics_file(studio, sid)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "my own\nlyrics here\n"  # stripped + trailing newline
    spec = _spec(studio, sid)
    assert spec["lyrics"] == "my own\nlyrics here\n"  # the queued job uses them (source 'user')


def test_url_without_lyrics(studio, fake_youtube):
    _add_url(studio)
    sid = _wait_song(studio)
    assert not _lyrics_file(studio, sid).exists()
    assert "lyrics" not in _spec(studio, sid)  # falls back to the online lookup chain


def test_url_with_empty_lyrics(studio, fake_youtube):
    _add_url(studio, lyrics="   \n  ")
    sid = _wait_song(studio)
    assert not _lyrics_file(studio, sid).exists()
    assert "lyrics" not in _spec(studio, sid)


# ---------------------------------------------------------------- files add
def test_files_with_lyrics(studio, tmp_path):
    sid = _upload(studio, tmp_path, lyrics="\nline one\nline two\n\n")
    p = _lyrics_file(studio, sid)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "line one\nline two\n"
    assert _spec(studio, sid)["lyrics"] == "line one\nline two\n"


def test_files_without_lyrics(studio, tmp_path):
    sid = _upload(studio, tmp_path)
    assert not _lyrics_file(studio, sid).exists()
    assert "lyrics" not in _spec(studio, sid)


def test_files_with_empty_lyrics(studio, tmp_path):
    sid = _upload(studio, tmp_path, lyrics="   ")
    assert not _lyrics_file(studio, sid).exists()
    assert "lyrics" not in _spec(studio, sid)


# ---------------------------------------------------------------- validation
def test_lyrics_too_long_rejected(studio, tmp_path):
    big = "x" * (MAX_LYRICS + 1)
    r = studio.post("/api/songs/url", json={"url": URL, "lyrics": big}, headers=H)
    assert r.status_code == 400, r.text
    assert "too long" in r.json()["detail"]
    f = tmp_path / "Some Artist - Some Song.wav"
    _wav(f)
    r = studio.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))],
                    data={"lyrics": big}, headers=H)
    assert r.status_code == 400, r.text
    assert "too long" in r.json()["detail"]
    assert studio.get("/api/songs").json() == []  # nothing was created


def test_lyrics_at_the_cap_accepted(studio, tmp_path):
    sid = _upload(studio, tmp_path, lyrics="x" * MAX_LYRICS)
    assert _lyrics_file(studio, sid).read_text(encoding="utf-8") == "x" * MAX_LYRICS + "\n"


# ---------------------------------------------------------------- existing endpoint unchanged
def test_set_lyrics_still_overwrites(studio, tmp_path):
    sid = _upload(studio, tmp_path)  # no lyrics at add time
    assert not _lyrics_file(studio, sid).exists()
    r = studio.post(f"/api/songs/{sid}/lyrics", json={"text": "first\n"}, headers=H)
    assert r.status_code == 200, r.text
    assert _lyrics_file(studio, sid).read_text(encoding="utf-8") == "first\n"
    r = studio.post(f"/api/songs/{sid}/lyrics", json={"text": "  second version  "}, headers=H)
    assert r.status_code == 200, r.text
    assert _lyrics_file(studio, sid).read_text(encoding="utf-8") == "second version\n"
    assert _spec(studio, sid)["lyrics"] == "second version\n"
    st = studio.get(f"/api/songs/{sid}").json()
    assert st["state"] == "queued" and st["priority"] >= 1  # re-queued, as before
