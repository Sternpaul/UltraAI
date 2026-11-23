"""Progress + interim results of the unattended library run (scripts/process_library.py).

    .venv/bin/python scripts/library_status.py [out/library]
"""
import json
import os
import statistics
import sys
from pathlib import Path

KEYS = ["word_recall", "onset_within_250ms", "onset_rel_within_100ms", "syllable_split_agreement",
        "line_f1", "pitch_class_acc"]


def main(out="out/library"):
    rows = [json.loads(ln) for ln in (Path(out) / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    total = len([p for p in Path("library").glob("*/*.txt")])
    secs = [r["seconds"] for r in rows if "error" not in r]
    left = total - len(rows)
    print(f"{len(rows)}/{total} songs processed, {sum('error' in r for r in rows)} failed")
    if secs:
        print(f"median {statistics.median(secs) / 60:.1f} min/song -> about {left * statistics.median(secs) / 3600:.0f} h left")
    sys.path.insert(0, os.path.dirname(__file__))
    from compare_runs import ref_shifted

    shifted = {r["name"] for r in rows if isinstance(r.get("m2"), dict) and "error" not in r["m2"]
               and ref_shifted(r["m2"])}
    for gen in ("m2", "m3"):
        ok = [r[gen] for r in rows if isinstance(r.get(gen), dict) and "error" not in r[gen]
              and r["name"] not in shifted]
        if ok:
            print(f"{gen}: {len(ok)} songs  " + "  ".join(
                f"{k}={100 * statistics.mean(m.get(k, 0) for m in ok):.1f}" for k in KEYS))
    if shifted:
        print(f"excluded {len(shifted)} songs whose reference looks shifted against the audio: "
              + ", ".join(sorted(s[:30] for s in shifted)))
    errs = [(r["name"], r.get("error") or r.get("m3", {}).get("error")) for r in rows
            if "error" in r or "error" in r.get("m3", {})]
    for name, e in errs[:15]:
        print(f"  ! {name[:40]:40} {e[:80]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
