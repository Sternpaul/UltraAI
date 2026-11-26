"""Validator problems in the Studio editor API (tmp library + token client only —
no songs/ folder, no network, no SSH).

PUT /doc returns the legacy ``problems`` strings AND ``problem_details``
({level, code, message, voice, line, beat}); GET /doc computes the same on the
saved txt, so validation issues are visible when the editor opens.  A clean
song returns empty lists."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import LocalProcessor  # noqa: E402

PORT = 8767
TOKEN = "test-token-0123456789abcdef"
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


def _make_song(lib) -> str:
    """A song folder with audio + txt, as if it had been processed already."""
    st = lib.create("Tester", "Test Song")
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


def test_get_doc_clean_song_has_no_problems(studio):
    sid = _make_song(studio.app.state.lib)
    r = studio.get(f"/api/songs/{sid}/doc")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["problems"] == [] and d["problem_details"] == []


def test_put_doc_overlap_returns_problems_and_details(studio):
    sid = _make_song(studio.app.state.lib)
    d = studio.get(f"/api/songs/{sid}/doc").json()
    assert d["problem_details"] == []
    doc = d["doc"]
    # BPM 300 / GAP 500 ms -> 0.05 s per beat; notes are at beats 0-4 and 4-8.
    # Drag the second note to beat 2: it now overlaps the first (ends at beat 4).
    n0, n1 = doc["voices"][0]["lines"][0]["notes"]
    assert n0["end"] <= n1["start"]
    n1["start"], n1["end"] = 0.60, 0.80
    r = studio.put(f"/api/songs/{sid}/doc",
                   json={"doc": doc, "note": "edit", "base_version": d["doc_version"]}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    # legacy field kept: list of strings
    assert body["problems"] and any("overlap" in p for p in body["problems"])
    # new field: structured details with jump-to coordinates
    (p,) = body["problem_details"]
    assert p["code"] == "overlap" and p["level"] == "warning"
    assert p["voice"] == 1 and p["line"] == 0 and p["beat"] == 2
    # message states the location as song time (GAP 500 ms + beat * 0.05 s), not beats/lines
    assert p["message"] == ("note at 0:00.6 overlaps the previous note (ends 0:00.7)"
                            " - shorten the previous note or move this one later")
    assert any("0:00.6" in s and "[P" not in s for s in body["problems"])
    # GET /doc on the saved file reports the same problems (visible on editor open)
    g = studio.get(f"/api/songs/{sid}/doc").json()
    assert g["problem_details"] == body["problem_details"]
    assert g["problems"] == body["problems"]


def test_put_doc_clean_edit_keeps_empty_problems(studio):
    sid = _make_song(studio.app.state.lib)
    d = studio.get(f"/api/songs/{sid}/doc").json()
    doc = d["doc"]
    doc["voices"][0]["lines"][0]["notes"][0]["text"] = "Lo "
    r = studio.put(f"/api/songs/{sid}/doc",
                   json={"doc": doc, "note": "edit", "base_version": d["doc_version"]}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["problems"] == [] and body["problem_details"] == []
    g = studio.get(f"/api/songs/{sid}/doc").json()
    assert g["problems"] == [] and g["problem_details"] == []
