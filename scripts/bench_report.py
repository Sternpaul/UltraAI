"""Summary of scripts/bench_models.py: mean metrics per config (complete configs only)."""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

KEYS = [("onset_within_250ms", "timing 250ms"), ("onset_rel_within_100ms", "timing rel 100ms"),
        ("word_recall", "words"), ("pitch_class_acc", "pitch"), ("pitch_class_acc_1", "pitch ±1")]


def main(out="out/bench"):
    rows = [json.loads(ln) for ln in (Path(out) / "results.jsonl").read_text().splitlines() if ln.strip()]
    songs = sorted({r["song"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        if "metrics" in r:
            by[r["config"]][r["song"]] = r
    print(f"{'config':58}" + "".join(f"{lbl:>17}" for _, lbl in KEYS) + "   min/song")
    for cfg, rs in by.items():
        mark = "" if len(rs) == len(songs) else f" ({len(rs)}/{len(songs)} songs)"
        vals = [100 * st.mean(r["metrics"].get(k, 0) for r in rs.values()) for k, _ in KEYS]
        secs = st.mean(r["seconds"] for r in rs.values()) / 60
        print(f"{(cfg + mark)[:58]:58}" + "".join(f"{v:17.1f}" for v in vals) + f"   {secs:8.1f}")
    errs = [r for r in rows if "error" in r]
    for r in errs:
        print("ERROR", r["config"], r["song"][:30], r["error"][:100])


if __name__ == "__main__":
    main(*sys.argv[1:])
