"""Compare library runs song by song.

    python scripts/compare_runs.py A:gen B:gen [metric]
    e.g.  compare_runs.py base:m2 fix1:m3        (A/B = tags under out/library/runs/)

Only songs scored in both are compared.  Songs whose reference looks shifted against the
audio (constant offset > 250 ms while offset-corrected timing is good) are listed apart:
their absolute timing says more about the reference file than about us.
"""
import json
import os
import statistics as st
import sys
from pathlib import Path


def load(spec, out=None):
    out = out or os.environ.get("ULTRAAI_OUT", "out/library")
    tag, gen = spec.split(":")
    p = Path(out) / "runs" / tag / "results.jsonl"
    rows = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            m = r.get(gen)
            if isinstance(m, dict) and "error" not in m:
                rows[r["name"]] = m
    return rows


def ref_shifted(m) -> bool:
    return abs(m.get("constant_offset_ms", 0)) > 250 and m.get("onset_rel_within_250ms", 0) > 0.8 \
        or m.get("onset_median_ms", 0) > 800 and m.get("word_recall", 0) > 0.9


def main(a, b, metric="onset_within_250ms"):
    A, B = load(a), load(b)
    names = sorted(set(A) & set(B))
    shifted = [n for n in names if ref_shifted(A[n]) or ref_shifted(B[n])]
    clean = [n for n in names if n not in shifted]
    keys = [metric, "onset_rel_within_100ms", "word_recall", "line_f1", "syllable_split_agreement", "pitch_class_acc"]
    print(f"{len(names)} songs in both, {len(shifted)} with shifted reference excluded: {', '.join(s[:25] for s in shifted)}")
    for k in keys:
        va, vb = [A[n].get(k, 0) for n in clean], [B[n].get(k, 0) for n in clean]
        print(f"  {k:28} {100 * st.mean(va):6.1f} -> {100 * st.mean(vb):6.1f}   (median {100 * st.median(va):.1f} -> {100 * st.median(vb):.1f})")
    d = sorted((B[n][metric] - A[n][metric], n) for n in clean)
    changed = [x for x in d if abs(x[0]) > 0.005]
    print(f"\n{len(changed)} songs changed by >0.5 points:")
    for x, n in changed:
        print(f"  {100 * x:+6.1f}  {100 * A[n][metric]:5.1f} -> {100 * B[n][metric]:5.1f}  {n[:60]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
