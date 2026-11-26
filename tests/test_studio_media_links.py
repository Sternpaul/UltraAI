"""Cover/video link overrides (USDB ``co=``/``v=``) in the Studio song info dialog.

PATCH /api/songs/{sid} accepts ``video_url``/``cover_url`` (empty = derive from the .ytid
sidecar / #VIDEOURL as before); the card shows the overrides plus the effective links, and
the USDB txt uses them.  Everything runs on a tiny fake library in tmp_path — no songs/
folder, no network, no SSH."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import LocalProcessor  # noqa: E402

PORT = 8766
TOKEN = "test-token-abcdef0123456789"
BASE = f"http://127.0.0.1:{PORT}"
H = {"x-ultraai": TOKEN}

YT = "UbstNV2FUNY"            # 11-char synthetic video id
YT2 = "dQw4w9WgXcQ"           # another 11-char synthetic video id
WATCH = f"https://www.youtube.com/watch?v={YT}"
WATCH2 = f"https://www.youtube.com/watch?v={YT2}"
THUMB = f"https://img.youtube.com/vi/{YT}/hqdefault.jpg"
COVER = "https://example.invalid/cover.jpg"

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


def _make_song(lib, artist="Tester", title="Test Song", source="") -> str:
    """A song folder with audio + txt, as if it had been processed already."""
    st = lib.create(artist, title, source=source)
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


def test_patch_media_links_persist_and_card_shows_effective(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib, source=WATCH)
    r = studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": WATCH2, "cover_url": COVER})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["video_url"] == WATCH2 and card["cover_url"] == COVER
    assert card["video_link"] == WATCH2 and card["cover_link"] == COVER
    # persisted: the GET endpoints return them too (single song and the list)
    got = studio.get(f"/api/songs/{sid}").json()
    assert got["video_url"] == WATCH2 and got["cover_url"] == COVER
    assert got["video_link"] == WATCH2 and got["cover_link"] == COVER
    lst = studio.get("/api/songs").json()
    assert next(c for c in lst if c["id"] == sid)["cover_link"] == COVER
    # the USDB txt uses the overrides: v=<id extracted from the override watch URL>, co=<override>
    u = studio.get(f"/api/songs/{sid}/usdb").text
    assert f"#VIDEO:v={YT2},co={COVER}" in u
    assert YT not in u and THUMB not in u


def test_usdb_override_accepts_short_and_watch_urls(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib, source=WATCH)
    r = studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": f"https://youtu.be/{YT2}?t=3"})
    assert r.status_code == 200, r.text
    u = studio.get(f"/api/songs/{sid}/usdb").text
    thumb2 = f"https://img.youtube.com/vi/{YT2}/hqdefault.jpg"
    # v= is the id extracted from the youtu.be override URL; no cover override -> the new video's thumbnail
    assert f"v={YT2}" in u and f"co={thumb2}" in u


def test_empty_override_falls_back_to_ytid_sidecar(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib, source="some local file.mp3")  # added from a file, not a link
    folder = lib.folder(sid)
    (folder / f"{folder.name}.ytid").write_text(YT + "\n", encoding="utf-8")
    card = studio.get(f"/api/songs/{sid}").json()
    assert card["video_url"] == "" and card["cover_url"] == ""
    assert card["video_link"] == WATCH and card["cover_link"] == THUMB
    u = studio.get(f"/api/songs/{sid}/usdb").text
    assert f"v={YT}" in u and f"co={THUMB}" in u
    # setting and then clearing an override falls back to the derived values again
    studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": WATCH2, "cover_url": COVER})
    r = studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": "", "cover_url": ""})
    assert r.status_code == 200
    card = r.json()
    assert card["video_url"] == "" and card["cover_url"] == ""
    assert card["video_link"] == WATCH and card["cover_link"] == THUMB


def test_invalid_media_links_rejected(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib, source=WATCH)
    assert studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": "ftp://x/y"}).status_code == 400
    assert studio.patch(f"/api/songs/{sid}", headers=H, json={"cover_url": "not a url"}).status_code == 400
    too_long = "https://e.co/" + "a" * 488  # 501 chars
    assert studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": too_long}).status_code == 400
    # 500 chars is fine; the rejected values did not stick
    ok = studio.patch(f"/api/songs/{sid}", headers=H, json={"cover_url": "https://e.co/" + "a" * 487})
    assert ok.status_code == 200 and len(ok.json()["cover_url"]) == 500
    card = studio.get(f"/api/songs/{sid}").json()
    assert card["video_url"] == "" and card["video_link"] == WATCH
    # no token -> 403
    assert studio.patch(f"/api/songs/{sid}", json={"cover_url": COVER}).status_code == 403


def test_rename_keeps_media_link_overrides(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib, artist="Old Artist", title="Old Title", source=WATCH)
    studio.patch(f"/api/songs/{sid}", headers=H, json={"video_url": f"https://youtu.be/{YT2}",
                                                       "cover_url": COVER})
    r = studio.patch(f"/api/songs/{sid}", headers=H, json={"artist": "New Artist", "title": "New Title"})
    assert r.status_code == 200, r.text
    assert lib.folder(sid).name == "New Artist - New Title"
    card = studio.get(f"/api/songs/{sid}").json()
    assert card["artist"] == "New Artist" and card["title"] == "New Title"
    assert card["video_url"] == f"https://youtu.be/{YT2}" and card["cover_url"] == COVER
    assert card["video_link"] == f"https://youtu.be/{YT2}" and card["cover_link"] == COVER
    u = studio.get(f"/api/songs/{sid}/usdb").text
    assert f"v={YT2}" in u and f"co={COVER}" in u
