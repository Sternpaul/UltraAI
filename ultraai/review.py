"""Local review server: listen to generated songs, nudge lines, save.

    ultraai review OUTPUT_FOLDER [--port 8765]

Binds to 127.0.0.1 only. From another machine use an SSH tunnel:
    ssh -L 8765:localhost:8765 <server>   then open http://localhost:8765
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ultrastar.model import Song
from .ultrastar.reader import read
from .ultrastar.validator import validate
from .ultrastar.writer import write

PAGE = Path(__file__).with_name("review_page.html")


def find_songs(root: Path) -> dict[str, Path]:
    """Song id (folder name) -> txt path."""
    out = {}
    for txt in sorted(root.glob("*/*.txt")):
        out[txt.parent.name] = txt
    return out


def sidecar(txt: Path) -> dict:
    p = txt.with_suffix(".ultraai.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def song_json(song_id: str, txt: Path) -> dict:
    song = read(txt)
    info = sidecar(txt)
    flags = info.get("lines", [])
    lines = []
    for li, line in enumerate(song.lines()):
        start, end = song.beat_to_seconds(line.start), song.beat_to_seconds(line.end)
        # match the sidecar line by time overlap (line indices may differ after edits)
        best = None
        for f in flags:
            ov = min(end, f["end"]) - max(start, f["start"])
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, f)
        flag = best[1] if best else {}
        lines.append({
            "index": li, "start": start, "end": end, "text": line.text,
            "review": bool(flag.get("review", False)), "confidence": flag.get("confidence"),
            "notes": [{"start": song.beat_to_seconds(n.start), "end": song.beat_to_seconds(n.end),
                       "text": n.text, "kind": n.kind, "pitch": n.pitch} for n in line.notes],
        })
    audio = song.audio or ""
    return {"id": song_id, "title": song.title, "artist": song.artist, "bpm": song.bpm,
            "gap": song.gap_ms, "beat_ms": song.seconds_per_beat * 1000,
            "audio": f"/media/{urllib.parse.quote(song_id)}/{urllib.parse.quote(audio)}",
            "lines": lines, "lyrics_source": info.get("lyrics_source"),
            "edited": txt.with_name(txt.name + ".bak").exists()}


def apply_edits(txt: Path, line_shifts_ms: dict[str, float], global_ms: float) -> list[str]:
    """Shift whole lines (and/or the song) in time; keeps a one-time backup of the original."""
    song: Song = read(txt)
    beat_ms = song.seconds_per_beat * 1000
    lines = song.lines()
    for key, ms in line_shifts_ms.items():
        li = int(key)
        if not 0 <= li < len(lines) or not ms:
            continue
        d = int(round(ms / beat_ms))
        for n in lines[li].notes:
            n.start += d
        if lines[li].break_beat is not None:
            lines[li].break_beat += d
        # the break before this line must stay between the previous line and this one
        if li > 0 and lines[li - 1].break_beat is not None:
            prev = lines[li - 1]
            prev.break_beat = max(prev.end, min(prev.break_beat, lines[li].start))
    # fix breaks of shifted lines against their next line
    for li, line in enumerate(lines[:-1]):
        nxt = lines[li + 1]
        if line.break_beat is None or not (line.end <= line.break_beat <= nxt.start):
            line.break_beat = max(line.end, min(nxt.start, (line.end + nxt.start) // 2))
    if global_ms:
        song.set("GAP", str(int(round(song.gap_ms + global_ms))))
    backup = txt.with_name(txt.name + ".bak")
    if not backup.exists():
        shutil.copy2(txt, backup)
    write(song, txt)
    return [str(i) for i in validate(read(txt)) if i.level in ("error", "warning")]


class Handler(BaseHTTPRequestHandler):
    root: Path

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        songs = find_songs(self.root)
        if url.path == "/":
            return self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/songs":
            items = []
            for sid, txt in songs.items():
                info = sidecar(txt)
                items.append({"id": sid, "review": sum(1 for x in info.get("lines", []) if x.get("review")),
                              "lines": len(info.get("lines", [])),
                              "edited": txt.with_name(txt.name + ".bak").exists()})
            items.sort(key=lambda x: (-x["review"], x["id"].lower()))
            return self._json(items)
        if url.path == "/api/song":
            sid = q.get("id", [""])[0]
            if sid not in songs:
                return self._json({"error": "unknown song"}, 404)
            return self._json(song_json(sid, songs[sid]))
        m = re.fullmatch(r"/media/([^/]+)/(.+)", url.path)
        if m:
            sid, name = (urllib.parse.unquote(x) for x in m.groups())
            if sid not in songs:
                return self._json({"error": "unknown song"}, 404)
            folder = songs[sid].parent.resolve()
            path = (folder / name).resolve()
            if path.parent != folder or not path.is_file():
                return self._json({"error": "not found"}, 404)
            return self._file(path)
        self._json({"error": "not found"}, 404)

    do_HEAD = do_GET

    def _file(self, path: Path):
        """Serve a file with HTTP Range support (needed for seeking in the audio element)."""
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() == ".m4a":
            ctype = "audio/mp4"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = HTTPStatus.OK
        if rng and (m := re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:  # suffix range
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = f.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        if url.path != "/api/save":
            return self._json({"error": "not found"}, 404)
        if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
            return self._json({"error": "cross-origin request refused"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        songs = find_songs(self.root)
        sid = body.get("id")
        if sid not in songs:
            return self._json({"error": "unknown song"}, 404)
        issues = apply_edits(songs[sid], body.get("line_shifts_ms", {}), float(body.get("global_ms", 0)))
        self._json({"ok": True, "issues": issues, "song": song_json(sid, songs[sid])})


def serve(root: Path, port: int = 8765, host: str = "127.0.0.1") -> None:
    handler = type("BoundHandler", (Handler,), {"root": root.resolve()})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Review {len(find_songs(root))} songs at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
