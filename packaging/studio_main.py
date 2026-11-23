"""Entry point of the packaged Windows app ("UltraAI Studio.exe").

    UltraAI Studio.exe                 start (or bring up) the Studio
    UltraAI Studio.exe --self-test F   start the backend headless, check it, write F, exit 0/1
"""
import os
import sys
import time
from pathlib import Path


def _frozen_setup() -> None:
    """Bundled tools (deno for yt-dlp) on PATH; output to a log file (windowed app has no console)."""
    exe_dir = Path(sys.executable).parent
    tools = exe_dir / "tools"
    if tools.exists():
        os.environ["PATH"] = f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"
    from ultraai.studio.config import config_dir

    log_dir = config_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "studio.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None or not sys.stdout.isatty():
        sys.stdout = log
    if sys.stderr is None or not sys.stderr.isatty():
        sys.stderr = log
    print(f"--- UltraAI Studio start {time.strftime('%Y-%m-%d %H:%M:%S')}")


def self_test(out: Path) -> int:
    """Start the backend on a free port with a temporary library and call its API."""
    import json
    import socket
    import tempfile
    import threading
    import urllib.request

    import uvicorn

    from ultraai.studio.app import create_app
    from ultraai.studio.config import Settings

    lines = []
    ok = True
    with tempfile.TemporaryDirectory() as tmp, socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        app = create_app(Settings(library=str(Path(tmp) / "lib"), processor="ssh"), start_queue=False)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"
        checks = {"/api/songs": lambda b: json.loads(b) == [], "/": lambda b: b"UltraAI Studio" in b,
                  "/editor.js": lambda b: b"openEditor" in b, "/app.css": lambda b: b"light-dark" in b}
        for path, test in checks.items():
            try:
                body = urllib.request.urlopen(base + path, timeout=10).read()
                good = bool(test(body))
            except Exception as exc:  # noqa: BLE001
                good, body = False, str(exc).encode()
            ok &= good
            lines.append(f"{'OK ' if good else 'BAD'} GET {path}")
        server.should_exit = True
    for mod in ("yt_dlp", "webview", "mutagen", "multipart"):
        try:
            __import__(mod)
            lines.append(f"OK  import {mod}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"BAD import {mod}: {exc}")
    import shutil

    for tool in ("deno", "ssh", "scp"):
        lines.append(f"{'OK ' if shutil.which(tool) else '-- '} tool {tool}: {shutil.which(tool)}")
    ok &= bool(shutil.which("deno"))
    lines.append("RESULT " + ("PASS" if ok else "FAIL"))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if getattr(sys, "frozen", False):
        _frozen_setup()
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        return self_test(Path(sys.argv[i + 1] if len(sys.argv) > i + 1 else "self-test.txt"))
    from ultraai.studio.app import run

    run(open_window=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
