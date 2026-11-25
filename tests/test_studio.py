"""Studio backend end to end, with a fake server (shell transport + fake ``ultraai``)."""
import json
import os
import shutil
import stat
import sys
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ultraai.studio.app import create_app  # noqa: E402
from ultraai.studio.config import Settings  # noqa: E402
from ultraai.studio.processors import ShellTransport, SshProcessor  # noqa: E402

_SONGS = sorted(Path("songs").glob("*/*.txt"))
if not _SONGS:  # the benchmark songs are not in the repository
    pytest.skip("needs the songs/ benchmark folder", allow_module_level=True)
SAMPLE = _SONGS[0]

FAKE = r'''#!{py}
import json, sys, time, os
from pathlib import Path
cmd = sys.argv[1]
if cmd == "--help":
    sys.exit(0)
job = Path(sys.argv[2])
def status(**kw):
    (job / "status.json").write_text(json.dumps(kw))
if cmd == "process":
    spec = json.loads((job / "job.json").read_text())
    status(state="running", stage="separating vocals")
    time.sleep(0.3)
    out = job / "out"; out.mkdir(exist_ok=True)
    (out / "editor.json").write_text("{{}}"); (out / "vocals.ogg").write_bytes(b"OggS")
    if spec["title"] == "nolyrics" and not spec.get("lyrics"):
        status(state="needs_lyrics", stage="no lyrics found", draft="la la la")
        sys.exit(0)
    (out / "song.txt").write_text(Path({sample!r}).read_text(encoding="utf-8", errors="replace").replace("#MP3:", "#MP3:x"), encoding="utf-8")
    (out / "song.ultraai.json").write_text(json.dumps({{"issues": [{{"kind": "timing", "start": 12.5, "end": 14, "text": "hello", "risk": 0.9, "reasons": ["r"]}}], "lyrics_source": "user" if spec.get("lyrics") else "LRCLIB"}}))
    status(state="done", stage="finished", issues=1)
elif cmd == "job-status":
    last = None
    while True:
        try:
            st = (job / "status.json").read_text()
        except OSError:
            st = json.dumps({{"state": "queued"}})
        if st != last:
            print(st, flush=True); last = st
        if json.loads(st)["state"] in ("done", "needs_lyrics", "error"):
            break
        time.sleep(0.1)
'''


@pytest.fixture
def studio(tmp_path):
    home = tmp_path / "server"
    tool = home / "UltraAI" / ".venv" / "bin" / "ultraai"
    tool.parent.mkdir(parents=True)
    tool.write_text(FAKE.format(py=sys.executable, sample=str(SAMPLE.resolve())))
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    settings = Settings(library=str(tmp_path / "lib"), processor="ssh", ssh_host="fake")

    def proc():
        p = SshProcessor(app.state.lib, ShellTransport(home))
        p.start_template = "cd {dir} && (nohup {ultraai} process jobs/{id} > /dev/null 2>&1 &)"
        return p

    app = create_app(settings, processor_factory=proc, token=TOKEN, port=8766)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        yield client, home


def _wav(path: Path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 800)


def _wait(client, sid, states, timeout=20):
    t = time.time() + timeout
    while time.time() < t:
        s = client.get(f"/api/songs/{sid}").json()
        if s["state"] in states:
            return s
        time.sleep(0.2)
    raise AssertionError(f"timeout, state {s['state']} {s.get('error')}")


TOKEN = "test-token-studio-0123456789"
H = {"x-ultraai": TOKEN}


def test_upload_process_edit_review(studio, tmp_path):
    client, home = studio
    f = tmp_path / "Some Artist - Some Song.wav"
    _wav(f)
    r = client.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))], headers=H)
    assert r.status_code == 200, r.text
    sid = r.json()[0]["id"]
    assert r.json()[0]["artist"] == "Some Artist"
    s = _wait(client, sid, ("needs_review", "error"))
    assert s["state"] == "needs_review", s
    assert s["open_issues"] == 1 and not list((home / "UltraAI" / "jobs").iterdir())  # remote cleaned up
    d = client.get(f"/api/songs/{sid}/doc").json()
    assert d["vocals"] and d["issues"][0]["key"]
    doc = d["doc"]
    n = doc["voices"][0]["lines"][0]["notes"][0]
    n["text"] = "Changed"
    n["start"] += 0.2
    r = client.put(f"/api/songs/{sid}/doc", json={"doc": doc, "note": "test edit", "base_version": d.get("doc_version", 0)},
                   headers=H)
    assert r.status_code == 200, r.text
    d2 = client.get(f"/api/songs/{sid}/doc").json()
    assert d2["doc"]["voices"][0]["lines"][0]["notes"][0]["text"] == "Changed"
    assert len(client.get(f"/api/songs/{sid}").json()["versions"]) == 2  # auto + edit
    client.post(f"/api/songs/{sid}/issues/dismiss", json={"key": d["issues"][0]["key"]}, headers=H)
    assert client.get(f"/api/songs/{sid}").json()["open_issues"] == 0
    assert client.post(f"/api/songs/{sid}/reviewed", headers=H).json()["state"] == "reviewed"
    # the audio is served with the MP3 header pointing at the imported file
    txt = next(p for p in (Path(client.app.state.settings.library)).glob("*/*.txt"))
    assert f"#MP3:{txt.stem}.wav" in txt.read_text(encoding="utf-8")
    assert client.get(f"/api/songs/{sid}/media/audio").status_code == 200


def test_missing_lyrics_then_pasted(studio, tmp_path):
    client, _ = studio
    f = tmp_path / "Band - nolyrics.wav"
    _wav(f)
    sid = client.post("/api/songs/files", files=[("files", (f.name, f.read_bytes(), "audio/wav"))],
                      headers=H).json()[0]["id"]
    s = _wait(client, sid, ("needs_lyrics", "error"))
    assert s["state"] == "needs_lyrics" and s["lyrics_draft"] == "la la la"
    client.post(f"/api/songs/{sid}/lyrics", json={"text": "my own\nlyrics"}, headers=H)
    s = _wait(client, sid, ("needs_review", "error"))
    assert s["state"] == "needs_review" and s["lyrics_source"] == "user"


def test_mutations_need_header(studio):
    client, _ = studio
    assert client.post("/api/songs/url", json={"url": "https://x"}).status_code == 403


def test_guess_artist_title():
    from ultraai.studio.importer import guess_artist_title

    assert guess_artist_title("Agnes - Release Me (Official Video)") == ("Agnes", "Release Me")
    assert guess_artist_title("a-ha – Take On Me [Official 4K Music Video]") == ("a-ha", "Take On Me")
    assert guess_artist_title("Just A Title") == ("", "Just A Title")


def test_media_headers(tmp_path):
    from ultraai.studio.store import Library
    from ultraai.ultrastar.reader import read

    lib = Library(tmp_path / "lib")
    st = lib.create("A", "B", source="https://example.invalid/v")
    folder = lib.folder(st.id)
    (folder / "A - B.txt").write_text(SAMPLE.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    for name in ("x.m4a", "v.mp4", "c.jpg"):
        (tmp_path / name).write_bytes(b"0")
    lib.add_audio(st.id, tmp_path / "x.m4a")
    lib.add_media(st.id, tmp_path / "v.mp4", "VD", "video")
    lib.add_media(st.id, tmp_path / "c.jpg", "CO", "cover")
    lib.apply_media_headers(st.id)
    song = read(folder / "A - B.txt")
    assert song.get("MP3") == "A - B.m4a" and song.get("VIDEO") == "A - B [VD].mp4"
    assert song.get("COVER") == "A - B [CO].jpg" and song.get("VIDEOGAP") is not None


def test_rename_and_usdb(tmp_path):
    from ultraai.studio.store import Library, usdb_text, youtube_id
    from ultraai.ultrastar.reader import read

    lib = Library(tmp_path / "lib")
    url = "https://www.youtube.com/watch?v=UbstNV2FUNY&list=x"
    st = lib.create("", url, source=url)
    folder = lib.folder(st.id)
    assert folder.name.startswith(" -") is False and "youtube" in folder.name
    (folder / f"{folder.name}.txt").write_text(SAMPLE.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    (tmp_path / "a.m4a").write_bytes(b"0")
    lib.add_audio(st.id, tmp_path / "a.m4a")
    lib.update(st.id, artist="Kingfishr", title="The Blade")
    new = lib.rename_to_meta(st.id)
    assert new.name == "Kingfishr - The Blade"
    assert sorted(p.name for p in new.iterdir() if p.is_file()) == ["Kingfishr - The Blade.m4a", "Kingfishr - The Blade.txt"]
    song = read(new / "Kingfishr - The Blade.txt")
    assert song.get("MP3") == "Kingfishr - The Blade.m4a" and song.get("ARTIST") == "Kingfishr"
    assert song.get("VIDEOURL") == "https://www.youtube.com/watch?v=UbstNV2FUNY"
    assert youtube_id("https://youtu.be/UbstNV2FUNY?t=3") == "UbstNV2FUNY"
    u = usdb_text((new / "Kingfishr - The Blade.txt").read_text(encoding="utf-8"), url)
    assert "#VIDEO:v=UbstNV2FUNY,co=https://img.youtube.com/vi/UbstNV2FUNY/hqdefault.jpg" in u and "#VIDEOURL" not in u
