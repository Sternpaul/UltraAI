"""Per-song golden-notes setting: PUT /doc accepts an optional ``golden`` field
({preset, share}), persists it in .studio/song.json and returns it on GET /doc and
GET /api/songs/{sid}.  Invalid preset or share -> 400.  Tiny synthetic library in
tmp_path — no songs/ folder, no network, no SSH."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import LocalProcessor  # noqa: E402
from ultraai.studio.store import Library  # noqa: E402

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


def _put_doc(client, sid, golden="omit", **extra):
    """PUT /doc with the current doc; ``golden="omit"`` sends no golden field."""
    g = client.get(f"/api/songs/{sid}/doc").json()
    body = {"doc": g["doc"], "base_version": g["doc_version"], "note": "edit", **extra}
    if golden != "omit":
        body["golden"] = golden
    return client.put(f"/api/songs/{sid}/doc", json=body, headers=H)


def test_golden_defaults_and_persist_roundtrip(studio, cfg):
    sid = _make_song(studio.app.state.lib)

    g = studio.get(f"/api/songs/{sid}/doc").json()
    assert g["golden"] == {"preset": "", "share": 0.04}          # defaults before any choice
    s = studio.get(f"/api/songs/{sid}").json()
    assert s["golden_preset"] == "" and s["golden_share"] == 0.04

    r = _put_doc(studio, sid, golden={"preset": "hard", "share": 0.06})
    assert r.status_code == 200, r.text

    # persisted server-side in .studio/song.json
    state = json.loads((studio.app.state.lib.folder(sid) / ".studio" / "song.json").read_text(encoding="utf-8"))
    assert state["golden_preset"] == "hard" and state["golden_share"] == 0.06

    # returned by GET /doc and GET /api/songs/{sid}
    assert studio.get(f"/api/songs/{sid}/doc").json()["golden"] == {"preset": "hard", "share": 0.06}
    s = studio.get(f"/api/songs/{sid}").json()
    assert s["golden_preset"] == "hard" and s["golden_share"] == 0.06

    # "custom" (hand-toggled notes) is a valid preset too
    r = _put_doc(studio, sid, golden={"preset": "custom", "share": 0.06})
    assert r.status_code == 200, r.text
    assert studio.get(f"/api/songs/{sid}/doc").json()["golden"]["preset"] == "custom"


def test_put_doc_without_golden_keeps_the_saved_choice(studio, cfg):
    sid = _make_song(studio.app.state.lib)
    assert _put_doc(studio, sid, golden={"preset": "mix", "share": 0.08}).status_code == 200
    r = _put_doc(studio, sid)                                     # plain edit: no golden field
    assert r.status_code == 200, r.text
    assert studio.get(f"/api/songs/{sid}/doc").json()["golden"] == {"preset": "mix", "share": 0.08}


@pytest.mark.parametrize("preset", ["bogus", "HARD", "*", 42, None])
def test_invalid_preset_is_400_and_not_saved(studio, cfg, preset):
    sid = _make_song(studio.app.state.lib)
    assert _put_doc(studio, sid, golden={"preset": "held", "share": 0.04}).status_code == 200
    r = _put_doc(studio, sid, golden={"preset": preset, "share": 0.04})
    assert r.status_code == 400, r.text
    state = json.loads((studio.app.state.lib.folder(sid) / ".studio" / "song.json").read_text(encoding="utf-8"))
    assert state["golden_preset"] == "held"                       # unchanged


@pytest.mark.parametrize("share", [0, -0.1, 0.51, 1, "4%", None, True])
def test_share_out_of_range_is_400(studio, cfg, share):
    sid = _make_song(studio.app.state.lib)
    r = _put_doc(studio, sid, golden={"preset": "high", "share": share})
    assert r.status_code == 400, r.text
    state = json.loads((studio.app.state.lib.folder(sid) / ".studio" / "song.json").read_text(encoding="utf-8"))
    assert state["golden_preset"] == "" and state["golden_share"] == 0.04


def test_share_upper_bound_is_accepted(studio, cfg):
    sid = _make_song(studio.app.state.lib)
    r = _put_doc(studio, sid, golden={"preset": "none", "share": 0.5})
    assert r.status_code == 200, r.text
    assert studio.get(f"/api/songs/{sid}/doc").json()["golden"] == {"preset": "none", "share": 0.5}


def test_golden_put_requires_the_token(studio, cfg):
    sid = _make_song(studio.app.state.lib)
    g = studio.get(f"/api/songs/{sid}/doc").json()
    body = {"doc": g["doc"], "base_version": g["doc_version"], "golden": {"preset": "mix", "share": 0.04}}
    assert studio.put(f"/api/songs/{sid}/doc", json=body).status_code == 403
