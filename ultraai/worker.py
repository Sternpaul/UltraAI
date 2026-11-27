"""Processing worker: one job folder in, a finished UltraStar song (+ editor data) out.

    ultraai process JOBDIR

JOBDIR/job.json (written by the Studio)::

    {"id": "...", "audio": "song.m4a", "artist": "...", "title": "...",
     "language": "English" | "" (auto), "lyrics": "plain text or LRC" | null,
     "separation": "melband", "greek_script": false}

Results in JOBDIR/out/: song.txt, song.ultraai.json (issues, sources), vocals.ogg (for the
editor), editor.json (pitch curve, waveform peaks, Whisper words).  Progress in
JOBDIR/status.json: {"state": queued|running|done|needs_lyrics|error, "stage", "error", ...}.
Jobs are serialized per audio file (sharded lock files under CACHE_DIR/locks/);
ULTRAAI_MAX_JOBS bounds how many different songs run at once on this machine (default 1 =
one at a time, as before).  Expensive steps are cached per audio file,
so re-running a job with new lyrics takes seconds.

Lyrics come from the job's "lyrics" (user-pasted) when present, else LRCLIB, else —
as a last resort — YouTube subtitles when a video id is known for the audio (a .ytid
sidecar, a #VIDEO:v= header, or a search when yt_subs_search is on).  Subtitles are
last because caption text rarely matches the sung lyrics (measured, see BENCHMARK.md).
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from . import config
from .audio import CACHE_DIR, DEFAULT_SEPARATION, file_hash

STATES = ("queued", "running", "done", "needs_lyrics", "error")


def write_status(job: Path, state: str, **kw) -> None:
    st = {"state": state, "updated": time.time(), **kw}
    tmp = job / "status.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, job / "status.json")


def read_status(job: Path) -> dict:
    try:
        return json.loads((job / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "queued"}


def _acquire(f, blocking: bool = True) -> bool:
    """flock(LOCK_EX) on an open file (POSIX). Isolated as a seam so tests can fake it."""
    import fcntl

    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except OSError:
        return False
    return True


def _release(f) -> None:
    import fcntl

    try:
        fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


class _Lock:
    """Exclusive lock on a file: the global lock, or a per-audio-hash lock."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        if not _acquire(self.f):  # as before: a job never proceeds without its lock
            self.f.close()
            raise OSError(f"cannot acquire lock: {self.path}")
        return self

    def __exit__(self, *exc):
        _release(self.f)
        self.f.close()


class _Slots:
    """Machine-wide counting semaphore: at most ``n`` of the slot lock files held at once."""

    def __init__(self, n: int, dir: Path | None = None):
        self.n = n
        self.dir = dir if dir is not None else SLOTS_DIR
        self.f = None

    def __enter__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        while self.f is None:
            for i in range(self.n):
                f = open(self.dir / f"slot-{i}.lock", "w")
                if _acquire(f, blocking=False):
                    self.f = f
                    return self
                f.close()
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        if self.f is not None:
            _release(self.f)
            self.f.close()
            self.f = None


LOCKS_DIR = CACHE_DIR / "locks"
SLOTS_DIR = LOCKS_DIR / "slots"
GLOBAL_LOCK = CACHE_DIR / "worker.lock"  # max_jobs=1: the whole job; max_jobs>1: shared resources only
PAUSED_FILE = CACHE_DIR / "background.paused"  # background units frozen by jobs, not yet thawed
PRESENCE_DIR = LOCKS_DIR / "background"  # per-job flock files: which jobs are inside _background_guard
_guard_ids = itertools.count()  # unique _background_guard presence file names per process


def max_jobs() -> int:
    """ULTRAAI_MAX_JOBS (>= 1); unreadable values fall back to 1 (one job at a time, as before)."""
    try:
        return max(1, config.get_int("max_jobs"))
    except ValueError:
        return 1


def subprocess_env() -> dict:
    """Environment for subprocesses spawned by the heavy steps: with several jobs in parallel
    (ULTRAAI_MAX_JOBS > 1) each one gets cores/max_jobs OpenMP threads.  os.environ itself is
    never modified; max_jobs=1 adds nothing (exactly as before)."""
    env = dict(os.environ)
    mj = max_jobs()
    if mj > 1 and "OMP_NUM_THREADS" not in env:
        env["OMP_NUM_THREADS"] = str(max(1, (os.cpu_count() or 1) // mj))
    return env


def _audio_hash(job: Path, spec: dict) -> str:
    """sha1[:16] of the job's audio file — the cache/work dir key (audio.file_hash)."""
    try:
        return file_hash(job / str(spec.get("audio") or ""))
    except OSError:
        return hashlib.sha1(str(spec.get("id") or "job").encode("utf-8")).hexdigest()[:16]


@contextlib.contextmanager
def _job_locks(job: Path, spec: dict):
    """Hold the job's locks: serialize per audio hash, bound the machine to max_jobs jobs.

    max_jobs == 1: the legacy single global lock (worker.lock) — byte-identical to before.
    max_jobs > 1: a per-audio-hash lock (locks/<sha1[:16]>.lock) so the same song never runs
    twice (its cache dir is only touched under it), plus the slot semaphore for the global
    bound; the global lock is then only held briefly, for shared resources (_background_guard,
    _save_rates).
    """
    mj = max_jobs()
    if mj <= 1:
        with _Lock(GLOBAL_LOCK):
            yield
        return
    with _Lock(LOCKS_DIR / f"{_audio_hash(job, spec)}.lock"):
        with _Slots(mj):
            yield


def lyrics_candidates(text: str, artist: str, title: str):
    """User-supplied lyrics (plain lines or LRC) as a candidate list."""
    from .lrc import LrcResult, parse_lrc

    lines = parse_lrc(text)
    plain = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("[")]
    if lines:
        plain = [ln.text for ln in lines if ln.text.strip()]
    return [LrcResult(artist, title, 0.0, lines, plain)]


def lookup_lyrics(spec: dict, audio: Path, yt_meta: dict, pr: "Progress") -> tuple[list, str, list[float] | None]:
    """Lyrics lookup order: user-supplied > LRCLIB > YouTube subtitles (last resort, pr/18).

    A lookup *failure* (LRCLIB down, ffprobe missing, unreadable cache) must not kill the
    job: log it and fall through to the next source.
    """
    from .batch import yt_sub_lyrics
    from .lrc import fetch_candidates

    artist, title = spec.get("artist", ""), spec.get("title", "")
    if spec.get("lyrics"):
        pr.skip("lyrics", "your lyrics")
        return lyrics_candidates(spec["lyrics"], artist, title), "user", None
    pr.start("lyrics")
    line_times: list[float] | None = None
    try:
        cands, source = fetch_candidates(artist, title, audio), "LRCLIB"
    except Exception as exc:  # noqa: BLE001 - any lookup error falls through to the subs
        log.warning("lyrics lookup failed (%s: %s); trying YouTube subtitles", type(exc).__name__, exc)
        cands, source = [], "LRCLIB"
    if not cands:
        yt = yt_sub_lyrics(audio, yt_meta)
        if yt is not None:
            cands, line_times = yt
            source = "yt"
    return cands, source, line_times


# ------------------------------------------------------------------ progress
STAGES = [("separate", "Separating vocals"), ("analyse", "Analysing the vocals (pitch, sounds)"),
          ("checks", "Double-checking with other models"),
          ("lyrics", "Looking up lyrics"), ("listen", "Listening (speech recognition)"),
          ("align", "Aligning lyrics and notes")]
# seconds of work per second of audio on a small CPU server (learned per machine, see Progress)
PRIOR_RATE = {"separate:htdemucs": 1.6, "separate:htdemucs_ft": 5.0, "separate:melband": 6.0,
              "analyse": 0.5, "lyrics": 0.01, "listen": 0.35, "align": 0.12,
              "lead:kara2": 1.2, "lead:melkara": 6.0,
              "pitch:swift": 0.02, "pitch:pesto": 0.25, "pitch:crepe-tiny": 0.35, "pitch:crepe-full": 5.0}

# per-song options from the Studio (job.json) -> environment of this (one-job) process
OPTION_KEYS = ("pitch", "pitch_check", "sep_check", "lead", "notes")


def apply_options(spec: dict) -> dict:
    opts = dict(spec.get("options") or {})
    for k in OPTION_KEYS:
        v = opts.get(k)
        if isinstance(v, list):
            v = ",".join(v)
        env = config.env_of(k)
        if v:
            os.environ[env] = str(v)
        elif k in opts and k in ("pitch_check", "sep_check", "lead"):
            os.environ[env] = ""  # explicitly none (an empty list), not the registered default
        else:
            os.environ.pop(env, None)
    cookies = (spec.get("options") or {}).get("cookies") or spec.get("cookies")
    if cookies:  # a path, never contents: forwarded into this job's env when the job sets it
        os.environ[config.env_of("cookies")] = str(cookies)
    return opts
RATES_FILE = CACHE_DIR / "stage_rates.json"


def _atomic_json(path: Path, obj) -> None:
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _save_rates(rates: dict) -> None:
    """Persist learned stage rates atomically (tmp+replace).  With max_jobs > 1 the global lock
    is taken and the file re-read, so parallel workers merge their entries instead of
    clobbering each other; with max_jobs=1 the job already holds the global lock."""
    if max_jobs() > 1:
        with _Lock(GLOBAL_LOCK):
            try:
                rates = {**json.loads(RATES_FILE.read_text(encoding="utf-8")), **rates}
            except (OSError, ValueError):
                pass
            _atomic_json(RATES_FILE, rates)
    else:
        _atomic_json(RATES_FILE, rates)


def _env_list(key: str) -> list[str]:
    return config.get_list(key)
_current: "Progress | None" = None


class Progress:
    """Stage list with timing; status.json is rewritten every few seconds with elapsed time,
    the current stage's estimated fraction and the remaining time (ETA), so the Studio can
    draw a progress bar without depending on the server's clock."""

    def __init__(self, job: Path, duration: float, separation: str):
        import threading

        self.job, self.duration, self.sep = job, max(30.0, duration), separation
        try:
            self.rates = {**PRIOR_RATE, **json.loads(RATES_FILE.read_text())}
        except (OSError, ValueError):
            self.rates = dict(PRIOR_RATE)
        self.stages = [{"key": k, "label": lbl, "state": "pending", "seconds": 0.0,
                        "expected": round(self._rate(k) * self.duration, 1)} for k, lbl in STAGES]
        self.cur: dict | None = None
        self.t_cur = 0.0
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._beat, daemon=True).start()

    def _rate(self, key: str) -> float:
        from . import models

        def known(k: str, group: str, model: str) -> float:  # learned on this machine, else benchmark
            return float(self.rates.get(k) or models.rate(group, model))

        if key == "checks":  # extra separations + pitch models: depends on the options
            r = sum(known(f"separate:{m}", "separation", m) for m in _env_list("sep_check") if m != self.sep)
            return r + sum(models.rate("pitch", m) for m in _env_list("pitch_check"))
        if key == "separate":
            return known(f"separate:{self.sep}", "separation", self.sep)
        r = float(self.rates.get(key, PRIOR_RATE.get(key, 0.5)))
        if key == "analyse":
            if config.get("lead"):
                r += models.rate("lead", config.get("lead"))
            r += models.rate("pitch", config.get("pitch"))
        return r

    def start(self, key: str) -> None:
        with self.lock:
            self._close()
            self.cur = next(st for st in self.stages if st["key"] == key)
            self.cur["state"] = "running"
            self.t_cur = time.time()
        log.info("stage %s", key)
        self._write()

    def skip(self, key: str, why: str = "") -> None:
        with self.lock:
            st = next(st for st in self.stages if st["key"] == key)
            st.update(state="skipped", note=why)
        self._write()

    def _close(self) -> None:
        if self.cur is None:
            return
        took = time.time() - self.t_cur
        self.cur.update(state="done", seconds=round(took, 1))
        if self.cur["key"] != "checks" and took > max(2.0, 0.25 * self.cur["expected"]):  # real work: learn speed
            k = f"{self.cur['key']}:{self.sep}" if self.cur["key"] == "separate" else self.cur["key"]
            self.rates[k] = round(0.7 * self._rate(self.cur["key"]) + 0.3 * took / self.duration, 4)
            try:
                _save_rates(self.rates)
            except OSError:
                pass
        else:
            self.cur["note"] = "cached"
        self.cur = None

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            frac, eta = None, 0.0
            stages = [dict(st) for st in self.stages]
            for st in stages:
                if st["state"] == "running":
                    el = now - self.t_cur
                    st["seconds"] = round(el, 1)
                    frac = min(0.97, el / max(1.0, st["expected"]))
                    eta += max(st["expected"] - el, 0.1 * st["expected"])
                elif st["state"] == "pending":
                    eta += st["expected"]
            return {"stages": stages, "frac": None if frac is None else round(frac, 3), "eta": round(eta),
                    "elapsed": round(now - self.t0), "stage": self.cur["label"] if self.cur else ""}

    def _write(self, state: str = "running", **kw) -> None:
        write_status(self.job, state, **{**self.snapshot(), **kw})

    def _beat(self) -> None:
        while self.alive:
            time.sleep(3.0)
            if self.alive and self.cur is not None:
                self._write()

    def finish(self, state: str, **kw) -> None:
        with self.lock:
            self._close()
            self.alive = False
            for st in self.stages:
                if st["state"] == "pending":
                    st["state"] = "skipped"
        self._write(state, **kw)


# ------------------------------------------------------------------ logging
import logging  # noqa: E402

log = logging.getLogger("ultraai.worker")


def _job_logging(out: Path) -> logging.Handler:
    """Everything (DEBUG) into out/worker.log; the Studio shows it in its log view."""
    h = logging.FileHandler(out / "worker.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    h.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    for noisy in ("numba", "matplotlib", "urllib3", "PIL", "faster_whisper.transcribe"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    logging.captureWarnings(True)
    return h


# background work on the server that must give way completely while a song is processed
BACKGROUND_UNITS = config.get("background_units").split()  # read once at import (see config.py)


def _systemctl(*args: str) -> str:
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def pause_background() -> list[str]:
    """Freeze running background units (benchmarks, library runs); returns the ones *we* froze."""
    paused = []
    for unit in BACKGROUND_UNITS:
        state = _systemctl("show", unit, "-p", "ActiveState", "-p", "FreezerState", "--value").split()
        if len(state) == 2 and state[0] == "active" and state[1] == "running":
            _systemctl("freeze", unit)
            paused.append(unit)
    if paused:
        log.info("paused background work while processing: %s", ", ".join(paused))
    return paused


def resume_background(units: list[str]) -> None:
    for unit in units:
        _systemctl("thaw", unit)
    if units:
        log.info("resumed background work: %s", ", ".join(units))


def _read_paused() -> list[str]:
    """Background units frozen by job guards and not yet thawed (shared across processes)."""
    try:
        d = json.loads(PAUSED_FILE.read_text(encoding="utf-8"))
        return sorted({str(u) for u in d.get("units") or []})
    except (OSError, ValueError):
        return []


def _write_paused(units: list[str]) -> None:
    _atomic_json(PAUSED_FILE, {"units": units})


def _others_inside() -> bool:
    """True while another job is inside _background_guard: it holds the flock of its own
    file under PRESENCE_DIR.  The OS drops that flock when a process dies (Studio cancel
    kills the worker), so a killed job never counts as inside and needs no recovery —
    unlocked leftovers of dead jobs are removed here."""
    for p in PRESENCE_DIR.glob("*.lock"):
        try:
            f = open(p, "w")
        except OSError:
            return True  # unreadable: assume somebody is in there
        try:
            if not _acquire(f, blocking=False):
                return True
            try:
                f.close()
                p.unlink()
            except OSError:
                pass
        finally:
            _release(f)
    return False


@contextlib.contextmanager
def _background_guard():
    """Freeze the background systemd units while jobs run, without a shared refcount that
    a killed worker could leak (Studio cancel kills the process, no finally).  Each job
    freezes (pause_background is idempotent via FreezerState) and every job thaws what is
    recorded — but only when it is the last one to leave, i.e. no other guard still holds
    its presence flock (a per-job lock file: released by the OS even on SIGKILL; the
    freeze record and the entry/exit sections live under the global lock).  With
    max_jobs=1 this is the legacy behavior: pause on start, thaw on exit."""
    if max_jobs() <= 1:
        paused = pause_background()
        try:
            yield
        finally:
            resume_background(paused)
        return
    me = PRESENCE_DIR / f"job-{os.getpid()}-{next(_guard_ids)}.lock"
    with _Lock(GLOBAL_LOCK):
        me.parent.mkdir(parents=True, exist_ok=True)
        f = open(me, "w")  # our presence: held while the job runs, dropped by the OS on kill
        _acquire(f)
        try:
            _write_paused(sorted(set(_read_paused()) | set(pause_background())))
        except BaseException:
            _release(f)
            f.close()
            raise
    try:
        yield
    finally:
        with _Lock(GLOBAL_LOCK):
            _release(f)
            f.close()
            try:
                me.unlink()
            except OSError:
                pass
            if not _others_inside():
                thaw = _read_paused()
                try:
                    PAUSED_FILE.unlink()
                except OSError:
                    pass
                resume_background(thaw)


def process(job: Path) -> dict:
    job = Path(job)
    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
    out = job / "out"
    out.mkdir(exist_ok=True)
    handler = _job_logging(out)
    log.info("job %s: %s - %s (separation %s)", spec.get("id"), spec.get("artist"), spec.get("title"),
             spec.get("separation") or DEFAULT_SEPARATION)
    write_status(job, "queued", stage="Waiting for the server (another song is being processed)")
    try:
        with _job_locks(job, spec), _background_guard():
            try:
                result = _process(job, spec, out)
            except Exception as exc:  # noqa: BLE001 - reported to the Studio
                if _current is not None:
                    _current.alive = False
                log.exception("job failed")
                write_status(job, "error", error=f"{type(exc).__name__}: {exc}",
                             traceback=traceback.format_exc()[-3000:])
                raise
        return result
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def _process(job: Path, spec: dict, out: Path) -> dict:
    from .audio import separate
    from .batch import detect_language, expand_candidates, greeklish
    from .byproducts import write_byproducts
    from .generate import analyse, generate_from_lines, generate_sung
    from .lyrics import language_code
    from .transcribe import transcribe
    from .ultrastar.writer import write

    from .lrc import audio_duration

    audio = job / spec["audio"]
    sep = spec.get("separation") or DEFAULT_SEPARATION
    artist, title = spec.get("artist", ""), spec.get("title", "")
    t0 = time.time()
    duration = audio_duration(audio) or 240.0
    log.info("audio %s, %.0f s", audio.name, duration)
    global _current
    opts = apply_options(spec)
    log.info("options: %s", opts or "defaults")
    pr = _current = Progress(job, duration, sep)
    pr.start("separate")
    separate(audio, sep, need_instrumental=False, env=subprocess_env())
    pr.start("analyse")
    a = analyse(audio, sep)
    pitch_checks = [m for m in _env_list("pitch_check") if m != config.get("pitch")]
    sep_checks = [m for m in _env_list("sep_check") if m != sep]
    if pitch_checks or sep_checks:
        from .generate import pitch_track
        from .audio import load_mono

        pr.start("checks")  # compute (and cache) the second opinions; compared during alignment
        for m in pitch_checks:
            pitch_track(a.vocals, a.wave, m)
        for m in sep_checks:
            v2, _ = separate(audio, m, need_instrumental=False, env=subprocess_env())
            pitch_track(v2, load_mono(v2, 16000), config.get("pitch"))
    else:
        pr.skip("checks", "not chosen")
    # lyrics: user-supplied > LRCLIB > YouTube subs (last resort: caption text rarely
    # matches the sung lyrics — measured word_recall -60.8, see BENCHMARK.md)
    line_times: list[float] | None = None
    yt_meta = {"ARTIST": artist, "TITLE": title,
               "SOURCE": spec.get("source") or "", "YTID": spec.get("ytid") or ""}
    cands, source, line_times = lookup_lyrics(spec, audio, yt_meta, pr)
    log.info("lyrics: %s, %d version(s)", source, len(cands))
    cands = [c for c in cands if c.texts]
    lang_name = spec.get("language") or ""
    if cands and not lang_name:
        code, lang_name = detect_language(cands[0].texts)
    code = language_code(lang_name) if lang_name else None
    pr.start("listen")
    vocals16 = a.vocals.with_name(a.vocals.stem + ".mono16000.wav")
    asr = transcribe(vocals16, code, wave=a.wave)
    _editor_media(a, asr, out)
    if not cands:
        draft = "\n".join(s.text for s in asr if s.text)
        log.warning("no lyrics found for %s - %s", artist, title)
        pr.finish("needs_lyrics", stage="no lyrics found", draft=draft, seconds=round(time.time() - t0))
        return {"state": "needs_lyrics"}
    if code == "el" and "roman" not in lang_name.lower() and not spec.get("greek_script"):
        for c in cands:
            for ln in c.lines:
                ln.text = greeklish(ln.text)
            c.plain = [greeklish(t) for t in c.plain]
        lang_name = "Greek (romanized)"
    cands = expand_candidates(cands, code or "xx")
    pr.start("align")
    meta = {"ARTIST": artist, "TITLE": title, "LANGUAGE": lang_name}
    for k in ("YEAR", "GENRE", "EDITION"):
        if spec.get(k.lower()):
            meta[k] = str(spec[k.lower()])
    try:
        song, info = generate_sung(audio, cands, code or "xx", meta, separation=sep,
                                   line_times=line_times)
    except LookupError:
        song, info = generate_from_lines(audio, cands[0].texts, line_times, code or "xx", meta, sep)
    song.set("MP3", spec["audio"])
    song.set("AUDIO", spec["audio"])
    write(song, out / "song.txt")
    write_byproducts(song, out / "song.txt")
    side = {k: info.get(k) for k in ("issues", "words", "lines", "risk_model", "structure", "candidate",
                                      "candidates", "anchored", "asr_coverage", "heard", "low_confidence_lines",
                                      "song_warning")}
    side.update(lyrics_source=source, language=lang_name, separation=sep)
    side["lyrics"] = lyrics_summary(cands, info, source)
    from .batch import yt_sidecar_fields
    # same ids as the lyrics lookup: the ytid may live only in the job spec, and
    # yt_subs_search must not retry (nor resolve) it a second time here
    side.update(yt_sidecar_fields(audio, yt_meta, [w for s in asr for w in s.words]))
    (out / "song.ultraai.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    n_issues = sum(1 for i in info.get("issues", []))
    log.info("done in %.0f s: %d lines, %d issues, structure %s", time.time() - t0,
             len(info.get("lines", [])), n_issues, info.get("structure"))
    pr.finish("done", stage="finished", issues=n_issues, seconds=round(time.time() - t0))
    return {"state": "done", "issues": n_issues}


STRUCTURE_TEXT = {
    "lrc-confirmed": "speech recognition confirmed the lyrics' line order and timing",
    "sung": "line order adjusted to what is actually sung (repeats/missing lines)",
    "lrc": "speech recognition heard too little to check the lyrics",
}


def lyrics_summary(cands, info: dict, source: str) -> dict:
    """Which lyrics were used, for the Studio: source, matched artist/title, synced, versions."""
    k = int(info.get("candidate") or 0)
    c = cands[k] if 0 <= k < len(cands) else cands[0]
    return {"source": source, "artist": c.artist, "title": c.title, "duration": c.duration,
            "synced": bool(c.lines), "lines": len(c.texts), "version": k + 1, "versions": len(cands),
            "structure": info.get("structure"), "structure_text": STRUCTURE_TEXT.get(info.get("structure"), ""),
            "heard": info.get("asr_coverage"), "text": list(c.texts)}


PEAK_HOP_S = 0.01
PITCH_HOP_S = 0.02


def _editor_media(a, asr, out: Path) -> None:
    """Compressed vocals + pitch curve + waveform peaks + transcript for the editor."""
    import numpy as np

    ogg = out / "vocals.ogg"
    if not ogg.exists():
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(a.vocals), "-ac", "1",
                        "-c:a", "libopus", "-b:a", "64k", str(ogg)], check=True, env=subprocess_env())
    w = np.abs(np.asarray(a.wave, dtype=np.float32))
    hop = int(16000 * PEAK_HOP_S)
    n = len(w) // hop
    peaks = w[: n * hop].reshape(n, hop).max(1)
    peaks = np.clip(np.round(255 * peaks / max(1e-6, float(np.percentile(peaks, 99.5)))), 0, 255).astype(int)
    p = a.pitch
    grid = np.arange(0.0, float(p.times[-1]) if len(p.times) else 0.0, PITCH_HOP_S)
    idx = np.clip(np.searchsorted(p.times, grid), 0, max(0, len(p.times) - 1))
    voiced = p.voiced()
    midi = [round(float(p.midi[i]), 2) if len(p.times) and voiced[i] else None for i in idx]
    words = [[round(w.start, 3), round(w.end, 3), w.text, round(w.prob, 3)] for s in asr for w in s.words]
    (out / "editor.json").write_text(json.dumps(
        {"peaks_hop": PEAK_HOP_S, "peaks": peaks.tolist(), "pitch_hop": PITCH_HOP_S, "pitch": midi,
         "asr_words": words}, separators=(",", ":")), encoding="utf-8")


FINAL = ("done", "needs_lyrics", "error")


def follow(job: Path, poll: float = 2.0, timeout: float = 6 * 3600) -> None:
    """Print status.json as one JSON line whenever it changes, until the job has finished.
    The Studio keeps one SSH connection open on this instead of polling."""
    import sys

    last, t_end = None, time.time() + timeout
    while time.time() < t_end:
        st = read_status(Path(job))
        line = json.dumps(st, ensure_ascii=False)
        if line != last:
            print(line, flush=True)
            last = line
            sys.stdout.flush()
        if st.get("state") in FINAL:
            return
        time.sleep(poll)
