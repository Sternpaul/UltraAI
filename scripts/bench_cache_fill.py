"""Fill the analysis cache for a genre/language-diverse sample of library songs.

    python scripts/bench_cache_fill.py [library] [n=20] [--shard i/k] [--dry-run]

Picks ``n`` not-yet-cached songs with maximal genre+language spread (deterministic),
then runs only the cacheable pipeline steps per song (separate -> mono/emissions/pitch
-> beats -> ASR).  Generation + scoring is left to ``scripts/regen_library.py``, which
is fast once the cache exists and which is what the PR benchmark gate uses.

``--shard i/k`` processes slice ``i`` of ``k`` of the selection, so several processes can
fill in parallel (the cache is per-song-hash, no locking needed).  Resumable: songs whose
emissions+ASR are already cached are skipped.  Progress goes to ``fill-<i>-<k>.log`` in
the current directory.

Env: ``ULTRAAI_CACHE`` (cache dir), ``ULTRAAI_SEPARATION`` (default htdemucs — must match
the gate's separation model so regen_library picks the songs up).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import traceback
from pathlib import Path

os.environ.setdefault("ULTRAAI_SEPARATION", "htdemucs")

from ultraai.audio import work_dir  # noqa: E402
from ultraai.eval.harness import load_cases  # noqa: E402
from ultraai.generate import analyse  # noqa: E402
from ultraai.lyrics import language_code  # noqa: E402
from ultraai.tempo import detect_beats  # noqa: E402
from ultraai.transcribe import transcribe  # noqa: E402

SEP = os.environ["ULTRAAI_SEPARATION"]
MIN_FREE_GB = 3.0


def cached(wd: Path) -> bool:
    return (wd / f"vocals.{SEP}.emissions.pt").exists() and any(wd.glob("*.asr-*.json"))


def _lang(meta: dict) -> str:
    l = (meta.get("LANGUAGE") or "?").split(",")[0].strip().lower()
    return {"englisch": "english"}.get(l, l) or "?"


def _genre(meta: dict) -> str:
    return (meta.get("GENRE") or "?").strip().upper() or "?"


def select(cases, n: int) -> list:
    """Deterministic language-stratified (proportional to the library, min 1 per
    language, majority language capped at 90% so the sample mirrors the library's
    real language mix), genre-round-robin pick of up to ``n`` uncached cases."""
    todo = [c for c in cases if c.audio is not None and not cached(work_dir(c.audio))]
    by_lang: dict[str, list] = {}
    for c in todo:
        by_lang.setdefault(_lang(c.metadata), []).append(c)
    langs = sorted(by_lang, key=lambda l: (-len(by_lang[l]), l))
    alloc = {l: max(1, round(n * len(by_lang[l]) / max(1, len(todo)))) for l in langs}
    majority = max(alloc, key=lambda l: len(by_lang[l]), default=None)
    if majority is not None and alloc[majority] > max(1, round(9 * n / 10)):
        alloc[majority] = max(1, round(9 * n / 10))
    while sum(alloc.values()) > n:
        l = max((l for l in langs if alloc[l] > 1), key=lambda l: alloc[l], default=None)
        if l is None:
            break
        alloc[l] -= 1

    def take(bucket: dict[str, list], k: int) -> list:
        genres = sorted(bucket, key=lambda g: (-len(bucket[g]), g))
        out, idx = [], {g: 0 for g in genres}
        while len(out) < k and any(idx[g] < len(bucket[g]) for g in genres):
            for g in genres:
                if len(out) >= k:
                    break
                if idx[g] < len(bucket[g]):
                    out.append(bucket[g][idx[g]])
                    idx[g] += 1
        return out

    picked: list = []
    for l in langs:
        bucket: dict[str, list] = {}
        for c in by_lang[l]:
            bucket.setdefault(_genre(c.metadata), []).append(c)
        for g in bucket:
            bucket[g].sort(key=lambda c: c.name)
        picked.extend(take(bucket, alloc[l]))
    if len(picked) < n:  # top up from whatever is left, genre round-robin
        rest = [c for c in todo if c not in picked]
        bucket = {}
        for c in rest:
            bucket.setdefault(_genre(c.metadata), []).append(c)
        picked.extend(take(bucket, n - len(picked)))
    return picked[:n]


def log(logf: Path, msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(logf, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("library", nargs="?", default="library")
    ap.add_argument("n", nargs="?", type=int, default=20)
    ap.add_argument("--shard", default="0/1", help="i/k: process slice i of k of the selection")
    ap.add_argument("--dry-run", action="store_true", help="print the selection and exit")
    args = ap.parse_args()
    i, k = (int(x) for x in args.shard.split("/"))
    logf = Path(f"fill-{i}-{k}.log")

    picked = select(load_cases(args.library), args.n)[i::k]
    log(logf, f"start: {len(picked)} songs (shard {i}/{k}, target {args.n})")
    if args.dry_run:
        for c in picked:
            print(f"{c.name}\t{c.metadata.get('GENRE', '?')}\t{c.metadata.get('LANGUAGE', '?')}")
        return
    failures = 0
    for j, case in enumerate(picked, 1):
        free = shutil.disk_usage(args.library).free / 1e9
        if free < MIN_FREE_GB:
            log(logf, f"stopping: only {free:.1f} GB disk left")
            break
        t0 = time.time()
        row = {"name": case.name, "genre": case.metadata.get("GENRE"),
               "language": case.metadata.get("LANGUAGE")}
        try:
            a = analyse(case.audio, SEP)
            row["t_analyse"] = round(time.time() - t0)
            mono = a.vocals.with_name(a.vocals.stem + ".mono16000.wav")
            asr = transcribe(mono, language_code(case.metadata.get("LANGUAGE")), wave=a.wave)
            row["asr_words"] = sum(len(s.words) for s in asr)
            detect_beats(work_dir(case.audio) / "mix.wav")
            failures = 0
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()[-1500:]
            failures += 1
        row["seconds"] = round(time.time() - t0)
        with open(logf.with_suffix(".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        wd = work_dir(case.audio)
        for big in ("mix.wav", f"instrumental.{SEP}.wav"):
            (wd / big).unlink(missing_ok=True)
        log(logf, f"[{j}/{len(picked)}] {case.name[:50]}  {row['seconds']} s  {row.get('error', '')}")
        if failures >= 5:
            log(logf, "stopping: 5 failures in a row - see the log")
            break
    else:
        log(logf, "finished")


if __name__ == "__main__":
    main()
