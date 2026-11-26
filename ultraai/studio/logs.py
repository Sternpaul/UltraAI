"""Studio log: a rotating file in the settings folder + the last entries in memory for the
log view.  Levels (most to least detail): trace < debug < info < warning < error.
"trace" records everything: every request, every SSH command, all downloader output and
the full server-side log of each job."""

from __future__ import annotations

import collections
import itertools
import logging
import logging.handlers
import threading
import time
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
LEVELS = {"trace": TRACE, "debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
          "error": logging.ERROR}

log = logging.getLogger("ultraai")


class RingHandler(logging.Handler):
    def __init__(self, size: int = 8000):
        super().__init__(TRACE)
        self.buf: collections.deque = collections.deque(maxlen=size)
        self.ids = itertools.count(1)
        self.lock2 = threading.Lock()
        self._last_id = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            with self.lock2:
                eid = next(self.ids)
                self._last_id = eid
                self.buf.append({"id": eid, "t": record.created, "level": record.levelname.lower(),
                                 "n": record.levelno, "name": record.name, "msg": msg})
        except Exception:  # noqa: BLE001 - logging must never raise
            pass

    def since(self, after: int, min_level: int, limit: int = 2000) -> list[dict]:
        with self.lock2:
            out = [e for e in self.buf if e["id"] > after and e["n"] >= min_level]
        return out[-limit:]

    @property
    def last_id(self) -> int:
        """The id of the newest entry (0 when empty) — the SSE stream's log cursor."""
        with self.lock2:
            return self._last_id


ring = RingHandler()
_file: logging.Handler | None = None


def setup(level: str = "info", log_dir: Path | None = None) -> None:
    global _file
    root = logging.getLogger()
    if ring not in root.handlers:
        root.addHandler(ring)
    if log_dir is not None and _file is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _file = logging.handlers.RotatingFileHandler(log_dir / "studio.log", maxBytes=2_000_000, backupCount=5,
                                                     encoding="utf-8")
        _file.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(_file)
    set_level(level)


def set_level(level: str) -> None:
    n = LEVELS.get(level, logging.INFO)
    logging.getLogger().setLevel(n)
    for name in ("ultraai", "yt_dlp", "uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(n)
    # uvicorn's per-request lines and third-party chatter only at "trace"
    logging.getLogger("uvicorn.access").setLevel(TRACE if n <= TRACE else logging.WARNING)
    for noisy in ("multipart", "asyncio", "urllib3", "pywebview"):
        logging.getLogger(noisy).setLevel(n if n <= TRACE else max(n, logging.INFO))
    log.info("log level %s", level)


class YtdlpLogger:
    """yt-dlp's own messages: its debug output is 'trace', info is 'debug'."""

    def __init__(self):
        self.l = logging.getLogger("yt_dlp")

    def debug(self, msg):
        self.l.log(TRACE if msg.startswith("[debug]") else logging.DEBUG, msg)

    def info(self, msg):
        self.l.debug(msg)

    def warning(self, msg):
        self.l.warning(msg)

    def error(self, msg):
        self.l.error(msg)


def timed(what: str):
    """Context manager logging how long something took (debug)."""
    class _T:
        def __enter__(self):
            self.t = time.time()
            return self

        def __exit__(self, *exc):
            log.debug("%s took %.1f s", what, time.time() - self.t)
    return _T()
