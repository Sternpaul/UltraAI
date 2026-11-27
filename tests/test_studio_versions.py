"""Studio scan cache (mtime invalidation) and the version list/restore API.

Everything runs on a synthetic library in tmp_path — no songs/ folder, no network,
no SSH; the client carries the per-install token like tests/test_studio_security.py.
"""

import json
import os
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
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
    app = create_app(settings, processor_factory=lambda: None, start_queue=False, token=TOKEN, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield client


# ---------------------------------------------------------------- scan cache
def _add_song_folder(root: Path, k: int, with_txt: bool = True) -> str:
    """A library folder with .studio/song.json (+ a txt), without Library.create."""
    folder = root / f"Song {k:04d}"
    (folder / ".studio").mkdir(parents=True)
    if with_txt:
        (folder / f"Song {k:04d}.txt").write_text(f"#TITLE:Song {k}\nE\n", encoding="utf-8")
    sid = f"sid{k:06d}"
    (folder / ".studio" / "song.json").write_text(
        json.dumps({"id": sid, "artist": "A", "title": f"Song {k}"}), encoding="utf-8")
    return sid


def _build_lib(root: Path, n: int, with_txt: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for k in range(n):
        _add_song_folder(root, k, with_txt=with_txt)


def test_scan_cache_warm_reads_nothing_and_mtime_touch_does_not_reread(cfg):
    lib = Library(cfg / "lib")
    _build_lib(lib.root, 50)

    cold = lib.scan()
    assert len(cold) == 50
    reads_cold = lib.scan_reads
    assert reads_cold == 50  # cold scan read every song.json once

    assert lib.scan() == cold
    assert lib.scan_reads == reads_cold  # warm scan: iterdir+stat only, zero content reads

    # an mtime-only touch of the folder (or of the txt) must NOT re-read song.json
    folder = lib.root / "Song 0007"
    os.utime(folder, None)
    os.utime(folder / "Song 0007.txt", None)
    lib.scan()
    assert lib.scan_reads == reads_cold


def test_scan_cache_change_add_remove_reflected(cfg):
    lib = Library(cfg / "lib2")
    _build_lib(lib.root, 50)
    lib.scan()
    reads = lib.scan_reads

    # changed song.json (new id inside) -> re-read, next scan reflects it
    folder = lib.root / "Song 0007"
    sj = folder / ".studio" / "song.json"
    data = json.loads(sj.read_text(encoding="utf-8"))
    data["id"] = "newsid999"
    sj.write_text(json.dumps(data), encoding="utf-8")
    os.utime(sj, (time.time() + 5, time.time() + 5))  # same size, different mtime
    idx = lib.scan()
    assert "newsid999" in idx and "sid000007" not in idx
    assert lib.scan_reads == reads + 1  # exactly the one changed file was re-read

    # added folder -> the library root mtime changed -> full rebuild, new song visible
    sid50 = _add_song_folder(lib.root, 50)
    idx = lib.scan()
    assert sid50 in idx and len(idx) == 51
    assert lib.scan_reads > reads + 1  # full rebuild re-read everything

    # removed folder -> gone from the index
    shutil.rmtree(lib.root / "Song 0003")
    idx = lib.scan()
    assert "sid000003" not in idx and len(idx) == 50


def test_scan_cache_song_without_studio_state_gets_txt_id(cfg):
    lib = Library(cfg / "lib3")
    folder = lib.root / "Plain Song"
    folder.mkdir(parents=True)
    (folder / "Plain Song.txt").write_text("#TITLE:Plain\nE\n", encoding="utf-8")
    idx = lib.scan()
    assert len(idx) == 1 and next(iter(idx)).startswith("x")
    assert lib.scan_reads == 0  # no song.json anywhere: nothing to read
    assert lib.scan() == idx and lib.scan_reads == 0
    # adding song.json later -> its id wins
    (folder / ".studio").mkdir()
    (folder / ".studio" / "song.json").write_text(json.dumps({"id": "real1"}), encoding="utf-8")
    assert "real1" in lib.scan()


# ---------------------------------------------------------------- versions API
def test_versions_list_and_restore_roundtrip(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib)
    txt = lib.txt_path(sid)
    original = txt.read_text(encoding="utf-8")
    lib.save_version(sid, original, "auto")

    # an editor save: creates a version and bumps doc_version
    d = studio.get(f"/api/songs/{sid}/doc").json()
    assert d["doc_version"] == 0
    doc = d["doc"]
    doc["voices"][0]["lines"][0]["notes"][0]["text"] = "Edited"
    r = studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "note": "edit", "base_version": 0}, headers=H)
    assert r.status_code == 200 and r.json()["doc_version"] == 1, r.text
    edited = txt.read_text(encoding="utf-8")
    assert "Edited" in edited

    # GET versions: [{name, mtime, size, what}]
    r = studio.get(f"/api/songs/{sid}/versions")
    assert r.status_code == 200
    vs = r.json()
    assert [v["name"] for v in vs] == ["000-auto.txt", "001-edit.txt"]
    for v in vs:
        assert set(v) >= {"name", "mtime", "size", "what"}
        assert v["size"] > 0 and v["mtime"] > 0
    assert vs[0]["what"] == "auto" and vs[1]["what"] == "edit"

    # restore the first version: 200 (no base_version needed), backup saved, doc_version bumped
    r = studio.post(f"/api/songs/{sid}/versions/000-auto.txt/restore", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["restored"] == "000-auto.txt"
    assert body["doc_version"] == 2
    assert body["backup"] == "002-pre-restore.txt"

    # the txt is now the restored version's content, atomically
    assert txt.read_text(encoding="utf-8") == original
    assert not list(txt.parent.glob("*.tmp"))  # atomic write leaves no temp files

    # the backup version holds the pre-restore (edited) text
    vs2 = studio.get(f"/api/songs/{sid}/versions").json()
    assert [v["name"] for v in vs2] == ["000-auto.txt", "001-edit.txt", "002-pre-restore.txt"]
    backup = lib.studio_dir(sid) / "versions" / "002-pre-restore.txt"
    assert "Edited" in backup.read_text(encoding="utf-8")

    # doc_version bumped: a stale editor save now gets a 409 (no silent clobbering)
    assert studio.put(f"/api/songs/{sid}/doc", json={"doc": doc, "base_version": 0},
                      headers=H).status_code == 409
    d2 = studio.get(f"/api/songs/{sid}/doc").json()
    assert d2["doc_version"] == 2
    assert d2["doc"]["voices"][0]["lines"][0]["notes"][0]["text"] == "La"

    # restore requires the token like every other mutation
    assert studio.post(f"/api/songs/{sid}/versions/000-auto.txt/restore").status_code == 403
    # unknown song -> 404
    assert studio.get("/api/songs/nope/versions").status_code == 404
    assert studio.post("/api/songs/nope/versions/000-auto.txt/restore", headers=H).status_code == 404


def test_version_name_validation_and_traversal_rejected(studio):
    lib = studio.app.state.lib
    sid = _make_song(lib)
    lib.save_version(sid, "v0", "auto")
    before = sorted(p.name for p in (lib.studio_dir(sid) / "versions").glob("*"))

    # malformed names -> 400 (validated against ^[0-9]+-[A-Za-z0-9_.-]+$)
    for bad in ("no-dash", "x", "000-auto.txt extra", "..%5C..%5Csong.json"):
        r = studio.post(f"/api/songs/{sid}/versions/{bad}/restore", headers=H)
        assert r.status_code == 400, (bad, r.status_code, r.text)
    # a well-formed but missing name -> 404, not 400
    assert studio.post(f"/api/songs/{sid}/versions/009-nope.txt/restore",
                       headers=H).status_code == 404
    # percent-encoded slashes are normalized away by the HTTP client (never a 200); if they
    # ever reach the handler decoded, the regex rejects them there too
    for enc in ("..%2F000-auto.txt", "%2e%2e%2f%2e%2e%2fsong.json", "000-auto.txt%2F..%2Fx"):
        assert studio.post(f"/api/songs/{sid}/versions/{enc}/restore", headers=H).status_code != 200

    # store level: traversal is impossible for any hostile name, the file stays confined
    for evil in ("../000-auto.txt", "..\\..\\song.json", "000-auto.txt/../../x", "..", "a/b"):
        with pytest.raises(ValueError):
            lib.version_file(sid, evil)
    v = lib.version_file(sid, "000-auto.txt")
    assert v.name == "000-auto.txt"
    assert v.parent == (lib.studio_dir(sid) / "versions").resolve()

    # nothing was created or modified by the rejected requests
    after = sorted(p.name for p in (lib.studio_dir(sid) / "versions").glob("*"))
    assert before == after
    assert lib.txt_path(sid).read_text(encoding="utf-8").startswith("#TITLE:")


# ---------------------------------------------------------------- perf micro-benchmark
def test_warm_scan_of_1000_songs_under_0_2s(cfg):
    """1000 synthetic songs: cold scan reads all song.json, warm scans only stat."""
    lib = Library(cfg / "big")
    t0 = time.perf_counter()
    _build_lib(lib.root, 1000, with_txt=False)
    build_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    assert len(lib.scan()) == 1000
    cold_s = time.perf_counter() - t0
    assert lib.scan_reads == 1000

    warm = []
    for _ in range(2):
        t0 = time.perf_counter()
        assert len(lib.scan()) == 1000
        warm.append(time.perf_counter() - t0)
    assert lib.scan_reads == 1000  # warm scans re-read nothing
    assert min(warm) < 0.2, f"warm scan too slow: {warm} (build {build_s:.2f}s, cold {cold_s:.2f}s)"
    print(f"\n1000 songs: build {build_s:.2f}s, cold scan {cold_s:.3f}s, "
          f"warm scan min {min(warm):.3f}s / max {max(warm):.3f}s")
