"""Turn the model benchmark (out/bench/results.jsonl) into ultraai/model_bench.json for the
model picker.  Only configs measured on every benchmark song are exported.

    python scripts/bench_export.py [out/bench]
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ultraai.models import BENCH_FILE, LEAD_FILES  # noqa: E402

KEYS = {"timing": "onset_within_250ms", "timing_100": "onset_rel_within_100ms", "words": "word_recall",
        "pitch": "pitch_class_acc", "pitch_1": "pitch_class_acc_1"}


def main(out="out/bench"):
    rows = [json.loads(ln) for ln in (Path(out) / "results.jsonl").read_text().splitlines() if ln.strip()]
    songs = sorted({r["song"] for r in rows})
    by = defaultdict(dict)
    for r in rows:
        if "metrics" in r:
            by[r["config"]][r["song"]] = r
    res = {"songs": songs, "separation": {}, "pitch": {}, "lead": {}}
    for cfg, rs in by.items():
        if len(rs) < len(songs):
            continue
        group, key = cfg.split(":", 1)
        group = {"sep": "separation"}.get(group, group)
        key = LEAD_FILES.get(key, key) if group == "lead" else key
        stats = {k: round(100 * st.mean(r["metrics"].get(m, 0) for r in rs.values()), 1) for k, m in KEYS.items()}
        stats["min_per_song"] = round(st.mean(r["seconds"] for r in rs.values()) / 60, 1)
        stats["score"] = stats["timing"] + 0.5 * stats["pitch"]  # ordering in the picker
        res[group][key] = stats
    # the baseline run (HTDemucs + SwiftF0, no backing-vocal removal) is the "pitch:swift" config
    if "swift" in res["pitch"]:
        base = res["pitch"]["swift"]
        res["separation"].setdefault("htdemucs", {**base, "min_per_song": 7.0})
        res["lead"].setdefault("", base)
        res["baseline"] = base
    BENCH_FILE.write_text(json.dumps(res, indent=1))
    print(f"{BENCH_FILE}: " + ", ".join(f"{g} {len(res[g])}" for g in ("separation", "pitch", "lead")))


if __name__ == "__main__":
    main(*sys.argv[1:])
