"""Studio settings, stored per user (Windows: %APPDATA%\\UltraAI\\studio.json).

Also holds the per-install API token (``token.txt`` next to ``studio.json``): a random
secret generated on first run, mode 0600.  Mutating API requests must send it as the
``x-ultraai`` header, so a website open in your browser cannot drive the Studio API.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("ultraai.studio")


def config_dir() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    d = Path(base) if base else Path.home() / ".config"
    return d / "UltraAI"


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write ``data`` to ``path`` atomically: temp file in the same folder + os.replace,
    so a crash never leaves a half-written file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def quarantine(path: Path) -> Path:
    """Move a corrupt file out of the way: ``<name>.corrupt-<timestamp>`` next to it."""
    path = Path(path)
    dst = path.with_name(f"{path.name}.corrupt-{int(time.time() * 1000)}")
    k = 2
    while dst.exists():
        dst = path.with_name(f"{path.name}.corrupt-{int(time.time() * 1000)}-{k}")
        k += 1
    path.rename(dst)
    return dst


def token_path() -> Path:
    return config_dir() / "token.txt"


def load_token() -> str:
    """The per-install API token, created on first run (secrets.token_urlsafe(32), mode 0600).

    Refuses to return anything (and tells the caller to refuse to start) when the token
    file is missing and cannot be created: without it every mutating request would be open.
    """
    p = token_path()
    if p.exists():
        try:
            tok = p.read_text(encoding="utf-8").strip()
        except OSError:
            tok = ""
        if tok:
            try:
                os.chmod(p, 0o600)  # enforce even if the file was created with looser perms
            except OSError:
                pass
            return tok
    tok = secrets.token_urlsafe(32)
    try:
        atomic_write(p, tok.encode("utf-8"), mode=0o600)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create the Studio API token file {p}: {exc}. "
            "Fix the permissions of the config folder — refusing to start the API without a token."
        ) from exc
    log.info("created a new Studio API token in %s", p)
    return tok


@dataclass
class Settings:
    library: str = str(Path.home() / "UltraAI Songs")
    processor: str = "ssh"              # "ssh" | "local"
    ssh_host: str = ""                  # user@server or a Host alias from ~/.ssh/config
    ssh_port: int = 0
    remote_dir: str = "UltraAI"         # relative to the remote home
    remote_ultraai: str = ".venv/bin/ultraai"  # relative to remote_dir
    youtube_audio: str = "best"         # "best": usually Opus, remuxed to .ogg; "m4a": AAC
    youtube_video: str = "1080"         # "none" | "720" | "1080" | "best"  (music video for USDX)
    youtube_cover: bool = True          # thumbnail as #COVER
    separation: str = "htdemucs"        # "htdemucs": ~5 min, measured; "melband": slower, cleaner vocals
    log_level: str = "info"             # trace | debug | info | warning | error
    defaults: dict = field(default_factory=dict)  # processing options pre-selected for new songs
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_dir() / "studio.json"
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(d, dict):
                    raise ValueError("not a JSON object")
                return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
            except (ValueError, TypeError) as exc:
                dst = quarantine(path)
                log.warning("settings file %s was corrupt (%s); moved to %s, using defaults", path, exc, dst)
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "studio.json"
        atomic_write(path, json.dumps(asdict(self), indent=1).encode("utf-8"))
