"""Turn the model benchmark (out/bench/results.jsonl) into ultraai/model_bench.json for the
model picker.  Only configs measured on every benchmark song are exported.

    python scripts/bench_export.py [out/bench] [out/bench-depth]

The depth pass (more songs, fewer models; scripts/bench_queue.sh) goes into "wide": the
picker shows it first because 3 songs can mislead.  Songs whose reference file is shifted
against the audio are left out (scripts/compare_runs.ref_shifted).
"""
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ultraai.models import BENCH_FILE, LEAD_FILES  # noqa: E402
from scripts.compare_runs import ref_shifted  # noqa: E402

KEYS = {"timing": "onset_within_250ms", "timing_100": "onset_rel_within_100ms", "words": "word_recall",
        "pitch": "pitch_class_acc", "pitch_1": "pitch_class_acc_1"}


# pitch models on 25 songs (24 scored), measured on a desktop CPU (BENCHMARK.md "Depth pass")
PITCH_WIDE = {"swift": (71.2, 81.8), "crepe-tiny": (70.7, 82.1), "rmvpe": (70.6, 82.9), "vote": (70.4, 81.5),
              "fcpe": (70.0, 80.8), "pesto": (68.6, 80.5)}
PITCH_WIDE_SONGS = 24


def _mean(rs, k):
    v = [r["metrics"].get(k) for r in rs]
    v = [x for x in v if isinstance(x, float) and not math.isnan(x)]
    return round(100 * st.mean(v), 1) if v else 0.0


def wide(depth):
    """Depth-pass stats per config, on the songs every config finished (shifted references out)."""
    f = Path(depth) / "results.jsonl"
    if not f.exists():
        return {}
    by = defaultdict(dict)
    for ln in f.read_text().splitlines():
        r = json.loads(ln) if ln.strip() else {}
        if "metrics" in r:
            by[r["config"]][r["song"]] = r
    if not by:
        return {}
    songs = set.intersection(*(set(v) for v in by.values()))
    songs = {s for s in songs if not any(ref_shifted(by[c][s]["metrics"]) for c in by)}
    res = {"songs_n": len(songs), "separation": {}}
    for cfg, rs in by.items():
        group, key = cfg.split(":", 1)
        rows = [rs[s] for s in songs]
        stats = {k: _mean(rows, m) for k, m in KEYS.items()}
        stats["score"] = round(stats["timing"] + 0.5 * stats["pitch"], 2)
        if group == "sep":
            res["separation"][key] = stats
    return res


def main(out="out/bench", depth="out/bench-depth"):
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
        stats["score"] = round(stats["timing"] + 0.5 * stats["pitch"], 2)  # ordering in the picker
        stage_rows = [r["stages"] for r in rs.values() if "stages" in r]
        if stage_rows:
            stats["stages"] = {n: round(st.mean(s[n] for s in stage_rows if n in s), 1)
                               for n in sorted({k for s in stage_rows for k in s})}
        res[group][key] = stats
    # the baseline run (HTDemucs + SwiftF0, no backing-vocal removal) is the "pitch:swift" config
    if "swift" in res["pitch"]:
        base = res["pitch"]["swift"]
        res["separation"].setdefault("htdemucs", {**base, "min_per_song": 7.0})
        res["lead"].setdefault("", base)
        res["baseline"] = base
    w = wide(depth)
    w["pitch"] = {k: {"pitch": p, "pitch_1": p1, "score": 0.5 * p} for k, (p, p1) in PITCH_WIDE.items()}
    w["pitch_songs_n"] = PITCH_WIDE_SONGS
    res["wide"] = w
    BENCH_FILE.write_text(json.dumps(res, indent=1))
    print(f"{BENCH_FILE}: " + ", ".join(f"{g} {len(res[g])}" for g in ("separation", "pitch", "lead")))


if __name__ == "__main__":
    main(*sys.argv[1:])
