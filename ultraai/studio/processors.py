"""Where songs are processed: on this machine, or on a server over SSH.

Both build the same job folder (job.json + audio), run ``ultraai process`` on it and bring
back ``out/``.  The SSH processor uses the system ``ssh``/``scp`` (Windows ships OpenSSH), so
your existing keys and ~/.ssh/config apply.  Remote jobs run detached on the server
(systemd-run in the CPU-capped slice when available), so closing the Studio machine does not stop
them; the Studio re-attaches when it starts again.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .store import Library

FINAL = ("done", "needs_lyrics", "error")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashing


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, creationflags=_NO_WINDOW, **kw)


class Processor:
    """Base: job preparation and result ingestion shared by local and remote processing."""

    def __init__(self, lib: Library, log=print, separation: str | None = None):
        self.lib, self.log, self.separation = lib, log, separation

    def job_spec(self, sid: str, job_id: str) -> dict:
        st = self.lib.state(sid)
        spec = {"id": job_id, "audio": "audio" + Path(st.audio).suffix.lower(), "artist": st.artist,
                "title": st.title, "language": st.language}
        if self.separation:
            spec["separation"] = self.separation
        lyr = self.lib.studio_dir(sid) / "lyrics.txt"
        if lyr.exists() and lyr.read_text(encoding="utf-8").strip():
            spec["lyrics"] = lyr.read_text(encoding="utf-8")
        return spec

    def process(self, sid: str, stage=None) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def ingest(self, sid: str, out: Path, status: dict) -> str:
        """Copy a finished job's results into the song folder; returns the new state."""
        sd = self.lib.studio_dir(sid)
        for name in ("editor.json", "vocals.ogg"):
            if (out / name).exists():
                shutil.copy2(out / name, sd / name)
        if status.get("state") == "needs_lyrics":
            (sd / "lyrics_draft.txt").write_text(status.get("draft", ""), encoding="utf-8")
            self.lib.update(sid, state="needs_lyrics", stage="no lyrics found - paste them", error="")
            return "needs_lyrics"
        if status.get("state") != "done":
            self.lib.update(sid, state="error", stage="", error=status.get("error", "processing failed"))
            return "error"
        st = self.lib.state(sid)
        folder = self.lib.folder(sid)
        txt = folder / f"{folder.name}.txt"
        text = (out / "song.txt").read_text(encoding="utf-8")
        text = text.replace(f"#MP3:{_job_audio(text)}", f"#MP3:{st.audio}").replace(
            f"#AUDIO:{_job_audio(text, 'AUDIO')}", f"#AUDIO:{st.audio}")
        if txt.exists():
            self.lib.save_version(sid, txt.read_text(encoding="utf-8"), "before-reprocess")
        txt.write_text(text, encoding="utf-8")
        self.lib.save_version(sid, text, "auto")
        side = json.loads((out / "song.ultraai.json").read_text(encoding="utf-8"))
        txt.with_suffix(".ultraai.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
        n = len(side.get("issues", []))
        self.lib.apply_media_headers(sid)
        self.lib.update(sid, state="needs_review", stage="", error="", issues=n, dismissed=[],
                        lyrics_source=side.get("lyrics_source", ""), job={},
                        language=st.language or side.get("language", ""))
        return "needs_review"


def _job_audio(text: str, key: str = "MP3") -> str:
    for ln in text.splitlines():
        if ln.startswith(f"#{key}:"):
            return ln.split(":", 1)[1]
    return ""


class LocalProcessor(Processor):
    """Run the pipeline on this machine (needs the full worker install)."""

    def process(self, sid: str, stage=None) -> str:
        job = self.lib.studio_dir(sid) / "job"
        shutil.rmtree(job, ignore_errors=True)
        job.mkdir()
        spec = self.job_spec(sid, uuid.uuid4().hex[:12])
        shutil.copy2(self.lib.folder(sid) / self.lib.state(sid).audio, job / spec["audio"])
        (job / "job.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.Popen([sys.executable, "-m", "ultraai.cli", "process", str(job)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                creationflags=_NO_WINDOW)
        from ..worker import read_status

        last = None
        while proc.poll() is None:
            st = read_status(job)
            if stage and st.get("stage") != last:
                stage(st.get("stage", ""))
                last = st.get("stage")
            time.sleep(1.0)
        st = read_status(job)
        if st.get("state") not in FINAL:
            st = {"state": "error", "error": (proc.stderr.read() if proc.stderr else "")[-500:] or "worker crashed"}
        state = self.ingest(sid, job / "out", st)
        shutil.rmtree(job, ignore_errors=True)
        return state


class SshTransport:
    """Run commands / copy files on the server with the system OpenSSH client."""

    def __init__(self, host: str, port: int | None = None, extra: list[str] | None = None):
        self.host = host
        self.opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
        if port:
            self.opts += ["-p", str(port)]
        self.opts += extra or []

    def run(self, cmd: str, timeout: float = 120) -> str:
        r = _run(["ssh", *self.opts, self.host, cmd], timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"ssh {self.host}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def stream(self, cmd: str):
        """Yield output lines of a long-running remote command."""
        p = subprocess.Popen(["ssh", *self.opts, self.host, cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, creationflags=_NO_WINDOW)
        assert p.stdout is not None
        yield from p.stdout
        p.wait()
        if p.returncode not in (0, None):
            raise ConnectionError((p.stderr.read() if p.stderr else "").strip() or f"ssh exit {p.returncode}")

    def put(self, local: Path, remote: str) -> None:
        scp_opts = [o.replace("-p", "-P") if o == "-p" else o for o in self.opts]
        r = _run(["scp", "-q", *scp_opts, str(local), f"{self.host}:{remote}"], timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"upload failed: {r.stderr.strip()}")

    def get_dir(self, remote: str, local: Path) -> None:
        scp_opts = [o.replace("-p", "-P") if o == "-p" else o for o in self.opts]
        r = _run(["scp", "-q", "-r", *scp_opts, f"{self.host}:{remote}", str(local)], timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"download failed: {r.stderr.strip()}")


class ShellTransport(SshTransport):
    """Same interface on this machine (tests, or a Studio running on the server itself).
    ``home`` plays the role of the remote home directory."""

    def __init__(self, home: Path):
        self.home = Path(home)

    def run(self, cmd: str, timeout: float = 120) -> str:
        r = subprocess.run(["bash", "-c", cmd], cwd=self.home, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        return r.stdout

    def stream(self, cmd: str):
        p = subprocess.Popen(["bash", "-c", cmd], cwd=self.home, stdout=subprocess.PIPE, text=True)
        assert p.stdout is not None
        yield from p.stdout
        p.wait()

    def put(self, local: Path, remote: str) -> None:
        shutil.copy2(local, self.home / remote)

    def get_dir(self, remote: str, local: Path) -> None:
        shutil.copytree(self.home / remote, Path(local) / Path(remote).name, dirs_exist_ok=True)


# started detached on the server: in the CPU-capped slice if systemd is there, else nohup
REMOTE_START = ("cd {dir} && if command -v systemd-run >/dev/null && systemd-run --user --quiet --collect "
                "--slice=ultraai.slice --unit=ultraai-job-{id} -p CPUWeight=1000 -p WorkingDirectory=\"$PWD\" "
                "{ultraai} process jobs/{id} 2>/dev/null; then true; else "
                "nohup {ultraai} process jobs/{id} > jobs/{id}/log.txt 2>&1 < /dev/null & fi")


class SshProcessor(Processor):
    start_template = REMOTE_START

    def __init__(self, lib: Library, transport: SshTransport, remote_dir: str = "UltraAI",
                 ultraai: str = ".venv/bin/ultraai", log=print, separation: str | None = None):
        super().__init__(lib, log, separation)
        self.t, self.dir, self.ultraai = transport, remote_dir, ultraai

    def check(self) -> str:
        """Connection test for the settings page."""
        return self.t.run(f"cd {shlex.quote(self.dir)} && {self.ultraai} --help >/dev/null && echo ok").strip()

    def process(self, sid: str, stage=None) -> str:
        st = self.lib.state(sid)
        job_id = st.job.get("remote_id")
        jd = f"{self.dir}/jobs/{job_id}" if job_id else None
        if not job_id:  # new job: upload + start
            job_id = uuid.uuid4().hex[:12]
            jd = f"{self.dir}/jobs/{job_id}"
            spec = self.job_spec(sid, job_id)
            stage and stage("uploading")
            self.t.run(f"mkdir -p {shlex.quote(jd)}")
            with tempfile.TemporaryDirectory() as tmp:
                jf = Path(tmp) / "job.json"
                jf.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
                self.t.put(self.lib.folder(sid) / st.audio, f"{jd}/{spec['audio']}")
                self.t.put(jf, f"{jd}/job.json")
            self.t.run(self.start_template.format(dir=shlex.quote(self.dir), id=job_id, ultraai=self.ultraai))
            self.lib.update(sid, job={"remote_id": job_id, "started": time.time()})
        status = self._follow(jd, stage)
        with tempfile.TemporaryDirectory() as tmp:
            if status.get("state") in ("done", "needs_lyrics"):
                stage and stage("downloading results")
                self.t.get_dir(f"{jd}/out", Path(tmp))
            state = self.ingest(sid, Path(tmp) / "out", status)
        try:
            self.t.run(f"rm -rf {shlex.quote(jd)}")
        except RuntimeError:
            pass
        return state

    def _follow(self, jd: str, stage) -> dict:
        """Stream the job status; reconnect after network trouble."""
        last: dict = {}
        failures = 0
        while True:
            try:
                for line in self.t.stream(f"{shlex.quote(self._abs(self.ultraai))} job-status "
                                          f"{shlex.quote(jd)} --follow"):
                    try:
                        last = json.loads(line)
                    except ValueError:
                        continue
                    failures = 0
                    if stage and last.get("stage"):
                        stage(last["stage"])
                    if last.get("state") in FINAL:
                        return last
            except (ConnectionError, OSError, RuntimeError) as exc:
                self.log(f"lost connection to the server ({exc}); retrying")
            failures += 1
            if last.get("state") in FINAL:
                return last
            time.sleep(min(60, 5 * failures))

    def _abs(self, path: str) -> str:
        return path if path.startswith("/") else f"{self.dir}/{path}"
