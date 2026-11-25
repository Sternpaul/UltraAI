"""Studio settings, stored per user (Windows: %APPDATA%\\UltraAI\\studio.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    d = Path(base) if base else Path.home() / ".config"
    return d / "UltraAI"


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
    cookies: str = ""                   # path to a Netscape cookies.txt for yt-dlp (--cookies); the file is a secret, never logged
    separation: str = "htdemucs"        # "htdemucs": ~5 min, measured; "melband": slower, cleaner vocals
    log_level: str = "info"             # trace | debug | info | warning | error
    defaults: dict = field(default_factory=dict)  # processing options pre-selected for new songs
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_dir() / "studio.json"
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "studio.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
