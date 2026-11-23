"""Processing worker: one job folder in, a finished UltraStar song (+ editor data) out.

    ultraai process JOBDIR

JOBDIR/job.json (written by the Studio)::

    {"id": "...", "audio": "song.m4a", "artist": "...", "title": "...",
     "language": "English" | "" (auto), "lyrics": "plain text or LRC" | null,
     "separation": "melband", "greek_script": false}

Results in JOBDIR/out/: song.txt, song.ultraai.json (issues, sources), vocals.ogg (for the
editor), editor.json (pitch curve, waveform peaks, Whisper words).  Progress in
JOBDIR/status.json: {"state": queued|running|done|needs_lyrics|error, "stage", "error", ...}.
Jobs run one at a time per machine (file lock); expensive steps are cached per audio file,
so re-running a job with new lyrics takes seconds.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from .audio import CACHE_DIR, DEFAULT_SEPARATION

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


class _Lock:
    """Exclusive machine-wide lock: one heavy job at a time."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.f.close()


def lyrics_candidates(text: str, artist: str, title: str):
    """User-supplied lyrics (plain lines or LRC) as a candidate list."""
    from .lrc import LrcResult, parse_lrc

    lines = parse_lrc(text)
    plain = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("[")]
    if lines:
        plain = [ln.text for ln in lines if ln.text.strip()]
    return [LrcResult(artist, title, 0.0, lines, plain)]


def process(job: Path) -> dict:
    job = Path(job)
    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
    out = job / "out"
    out.mkdir(exist_ok=True)
    write_status(job, "queued", stage="waiting for the worker")
    with _Lock(CACHE_DIR / "worker.lock"):
        try:
            result = _process(job, spec, out)
        except Exception as exc:  # noqa: BLE001 - reported to the Studio
            write_status(job, "error", error=f"{type(exc).__name__}: {exc}",
                         traceback=traceback.format_exc()[-3000:])
            raise
    return result


def _process(job: Path, spec: dict, out: Path) -> dict:
    from .audio import separate
    from .batch import detect_language, greeklish
    from .generate import analyse, generate_from_lines, generate_sung
    from .lrc import fetch_candidates
    from .lyrics import language_code
    from .transcribe import transcribe
    from .ultrastar.writer import write

    audio = job / spec["audio"]
    sep = spec.get("separation") or DEFAULT_SEPARATION
    artist, title = spec.get("artist", ""), spec.get("title", "")
    t0 = time.time()
    write_status(job, "running", stage="separating vocals")
    separate(audio, sep, need_instrumental=False)
    write_status(job, "running", stage="analysing vocals")
    a = analyse(audio, sep)
    # lyrics: user-supplied, else LRCLIB
    if spec.get("lyrics"):
        cands, source = lyrics_candidates(spec["lyrics"], artist, title), "user"
    else:
        write_status(job, "running", stage="looking up lyrics")
        cands, source = fetch_candidates(artist, title, audio), "LRCLIB"
    cands = [c for c in cands if c.texts]
    lang_name = spec.get("language") or ""
    if cands and not lang_name:
        code, lang_name = detect_language(cands[0].texts)
    code = language_code(lang_name) if lang_name else None
    write_status(job, "running", stage="listening (speech recognition)")
    vocals16 = a.vocals.with_name(a.vocals.stem + ".mono16000.wav")
    asr = transcribe(vocals16, code, wave=a.wave)
    _editor_media(a, asr, out)
    if not cands:
        draft = "\n".join(s.text for s in asr if s.text)
        write_status(job, "needs_lyrics", stage="no lyrics found", draft=draft,
                     seconds=round(time.time() - t0))
        return {"state": "needs_lyrics"}
    if code == "el" and "roman" not in lang_name.lower() and not spec.get("greek_script"):
        for c in cands:
            for ln in c.lines:
                ln.text = greeklish(ln.text)
            c.plain = [greeklish(t) for t in c.plain]
        lang_name = "Greek (romanized)"
    write_status(job, "running", stage="aligning lyrics and notes")
    meta = {"ARTIST": artist, "TITLE": title, "LANGUAGE": lang_name}
    for k in ("YEAR", "GENRE", "EDITION"):
        if spec.get(k.lower()):
            meta[k] = str(spec[k.lower()])
    try:
        song, info = generate_sung(audio, cands, code or "xx", meta, separation=sep)
    except LookupError:
        song, info = generate_from_lines(audio, cands[0].texts, None, code or "xx", meta, sep)
    song.set("MP3", spec["audio"])
    song.set("AUDIO", spec["audio"])
    write(song, out / "song.txt")
    side = {k: info.get(k) for k in ("issues", "words", "lines", "risk_model", "structure", "candidate",
                                      "candidates", "anchored", "asr_coverage")}
    side.update(lyrics_source=source, language=lang_name, separation=sep)
    (out / "song.ultraai.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    n_issues = sum(1 for i in info.get("issues", []))
    write_status(job, "done", stage="finished", issues=n_issues, seconds=round(time.time() - t0))
    return {"state": "done", "issues": n_issues}


PEAK_HOP_S = 0.01
PITCH_HOP_S = 0.02


def _editor_media(a, asr, out: Path) -> None:
    """Compressed vocals + pitch curve + waveform peaks + transcript for the editor."""
    import numpy as np

    ogg = out / "vocals.ogg"
    if not ogg.exists():
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(a.vocals), "-ac", "1",
                        "-c:a", "libopus", "-b:a", "64k", str(ogg)], check=True)
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
