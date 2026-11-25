"""Re-generate + re-score library songs that are already analysed (cached vocals/ASR), fast.

    python scripts/regen_library.py TAG [gens=m2,m3] [library] [out/library]

Writes out/library/runs/TAG/{results.jsonl, m2/, m3/}.  Only songs whose separation cache
exists are processed, so this never triggers the slow separation step.  Use
scripts/compare_runs.py to compare two tags.
"""
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("ULTRAAI_SEPARATION", "htdemucs")
os.environ.setdefault("ULTRAAI_PITCH_CHECK", "")  # double checks only add warnings; keep gate runs fast

from ultraai.audio import work_dir  # noqa: E402
from ultraai.byproducts import write_byproducts  # noqa: E402
from ultraai.eval.harness import GENERATORS, load_cases  # noqa: E402
from ultraai.eval.metrics import compare  # noqa: E402
from ultraai.ultrastar.writer import write  # noqa: E402

SEP = os.environ["ULTRAAI_SEPARATION"]


def main(tag, gens="m2,m3", root="library", out="out/library"):
    dest = Path(out) / "runs" / tag
    dest.mkdir(parents=True, exist_ok=True)
    results = dest / "results.jsonl"
    done = set()
    if results.exists():
        done = {json.loads(ln)["name"] for ln in results.read_text(encoding="utf-8").splitlines() if ln.strip()}
    only = os.environ.get("ULTRAAI_ONLY")
    cases = [c for c in load_cases(root) if c.audio is not None
             and (work_dir(c.audio) / f"vocals.{SEP}.emissions.pt").exists()
             and any(work_dir(c.audio).glob("*.asr-*.json"))
             and c.name not in done and (not only or only.lower() in c.name.lower())]
    if os.environ.get("ULTRAAI_LIMIT"):
        cases = cases[: int(os.environ["ULTRAAI_LIMIT"]) - len(done)]
    print(f"{tag}: {len(cases)} cached songs to regenerate", flush=True)
    for case in cases:
        t0 = time.time()
        row = {"name": case.name, "language": case.metadata.get("LANGUAGE"),
               "duet": len(case.reference.voices) > 1}
        for gen in gens.split(","):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    hyp = GENERATORS[gen](case)
                (dest / gen).mkdir(exist_ok=True)
                write(hyp, dest / gen / f"{case.name}.txt")
                write_byproducts(hyp, dest / gen / f"{case.name}.txt")
                if getattr(hyp, "generation_info", None):
                    (dest / gen / f"{case.name}.ultraai.json").write_text(
                        json.dumps(hyp.generation_info, ensure_ascii=False), encoding="utf-8")
                row[gen] = {k: (round(v, 4) if isinstance(v, float) else v)
                            for k, v in compare(case.reference, hyp).items()}
                row[gen]["log"] = buf.getvalue().strip()[-300:]
            except Exception as exc:
                row[gen] = {"error": f"{type(exc).__name__}: {exc}"}
        row["seconds"] = round(time.time() - t0)
        with open(results, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{case.name[:50]:50} {row['seconds']:4} s  " + "  ".join(
            f"{g}={row[g].get('onset_within_250ms', float('nan')):.3f}" for g in gens.split(",")), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
