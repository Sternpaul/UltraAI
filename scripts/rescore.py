"""Re-score existing generated txt files against the references (no regeneration).

Usage: python scripts/rescore.py HYP_DIR [HYP_DIR ...]
"""
import sys
from pathlib import Path

from ultraai.eval.harness import load_cases
from ultraai.eval.metrics import compare
from ultraai.ultrastar.reader import read

KEYS = ["word_recall", "onset_median_ms", "onset_within_100ms", "onset_within_250ms",
        "onset_rel_within_100ms", "syllable_split_agreement", "line_f1", "pitch_class_acc"]


def score(hyp_dir):
    rows = {}
    for c in load_cases("songs"):
        p = Path(hyp_dir) / f"{c.name}.txt"
        if p.exists():
            rows[c.name] = compare(c.reference, read(p))
    return rows


if __name__ == "__main__":
    runs = {d: score(d) for d in sys.argv[1:]}
    names = sorted(set.intersection(*(set(r) for r in runs.values())))
    for d, rows in runs.items():
        print(f"{d}  ({len(names)} songs)")
        print("  " + "  ".join(f"{k}={sum(rows[n].get(k, 0) for n in names) / len(names) * (1 if k.endswith('_ms') else 100):.1f}" for k in KEYS))
    if len(runs) == 2:
        (a, ra), (b, rb) = runs.items()
        print("\nper song onset_within_250ms:")
        for n in names:
            print(f"  {n[:40]:40} {100*ra[n]['onset_within_250ms']:5.1f} -> {100*rb[n]['onset_within_250ms']:5.1f}")
