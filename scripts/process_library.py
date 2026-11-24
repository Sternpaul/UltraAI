"""Long unattended run over the song library: analyse, transcribe, generate (M2 + M3), score.

    python scripts/process_library.py [library] [out/library]

- resumable: songs with a result line in results.jsonl are skipped
- random but fixed order, so a partial run is a fair sample
- after each song the large intermediate wavs (mix, instrumental) are deleted to save disk;
  vocals, emissions, pitch, beats and transcript stay cached for later experiments
- one JSON line per song in results.jsonl (metrics or the error), progress in progress.log
"""
import contextlib
import io
import json
import os
import random
import shutil
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("ULTRAAI_SEPARATION", "htdemucs")

from ultraai.audio import work_dir  # noqa: E402
from ultraai.eval.harness import GENERATORS, load_cases  # noqa: E402
from ultraai.eval.metrics import compare  # noqa: E402
from ultraai.generate import analyse  # noqa: E402
from ultraai.lyrics import language_code  # noqa: E402
from ultraai.transcribe import transcribe  # noqa: E402
from ultraai.ultrastar.writer import write  # noqa: E402

SEP = os.environ["ULTRAAI_SEPARATION"]
MIN_FREE_GB = 3.0


def log(out: Path, msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(out / "progress.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(root="library", out="out/library"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results = out / "results.jsonl"
    done = set()
    if results.exists():
        done = {json.loads(ln)["name"] for ln in results.read_text(encoding="utf-8").splitlines() if ln.strip()}
    cases = [c for c in load_cases(root) if c.audio is not None]
    random.Random(42).shuffle(cases)
    todo = [c for c in cases if c.name not in done]
    log(out, f"start: {len(cases)} songs, {len(done)} done, {len(todo)} to go")
    failures = 0
    for k, case in enumerate(todo, 1):
        free = shutil.disk_usage(out).free / 1e9
        if free < MIN_FREE_GB:
            log(out, f"stopping: only {free:.1f} GB disk left")
            break
        t0 = time.time()
        row = {"name": case.name, "language": case.metadata.get("LANGUAGE"), "duet": len(case.reference.voices) > 1}
        try:
            a = analyse(case.audio, SEP)
            row["t_analyse"] = round(time.time() - t0)
            lang = language_code(case.metadata.get("LANGUAGE"))
            asr = transcribe(a.vocals.with_name(a.vocals.stem + ".mono16000.wav"), lang, wave=a.wave)
            row["asr_words"] = sum(len(s.words) for s in asr)
            row["t_asr"] = round(time.time() - t0) - row["t_analyse"]
            for gen in ("m2", "m3"):
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        hyp = GENERATORS[gen](case)
                    (out / gen).mkdir(exist_ok=True)
                    write(hyp, out / gen / f"{case.name}.txt")
                    row[gen] = {k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in compare(case.reference, hyp).items()}
                    row[gen]["log"] = buf.getvalue().strip()[-300:]
                except Exception as exc:  # e.g. no lyrics on LRCLIB
                    row[gen] = {"error": f"{type(exc).__name__}: {exc}"}
            failures = 0
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["traceback"] = traceback.format_exc()[-2000:]
            failures += 1
        row["seconds"] = round(time.time() - t0)
        with open(results, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        wd = work_dir(case.audio)
        for big in ("mix.wav", f"instrumental.{SEP}.wav"):
            (wd / big).unlink(missing_ok=True)
        m3 = row.get("m3", {})
        log(out, f"[{k}/{len(todo)}] {case.name[:50]}  {row['seconds']} s  "
                 f"m3 within250={m3.get('onset_within_250ms', float('nan')):.3f}  {row.get('error', '')}")
        if failures >= 5:
            log(out, "stopping: 5 songs in a row failed - something is broken, see results.jsonl")
            break
    else:
        log(out, "finished")


if __name__ == "__main__":
    main(*sys.argv[1:])
