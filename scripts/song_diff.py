"""Per-line timing diff of a generated song vs its reference.

Usage: python scripts/song_diff.py "Song name" [hyp_dir]
For each reference line: start time, mean onset error of matched words, matched/total,
reference text and the hypothesis words matched to it.  '<<' = off by >250 ms, '?' = <60% matched.
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from ultraai.eval.harness import load_cases
from ultraai.eval.metrics import match_words, timed_words
from ultraai.ultrastar.reader import read


def main(name, hyp_dir="out/m2_full/m2"):
    case = next(c for c in load_cases(__import__("os").environ.get("ULTRAAI_SONGS", "songs")) if c.name == name)
    hyp = read(Path(hyp_dir) / f"{name}.txt")
    rw, hw = timed_words(case.reference), timed_words(hyp)
    pairs = dict(match_words(rw, hw))
    by_line = defaultdict(list)
    for k, w in enumerate(rw):
        by_line[w.line_index].append(k)
    for li, idx in sorted(by_line.items()):
        errs = [hw[pairs[k]].start - rw[k].start for k in idx if k in pairs]
        e = f"{np.median(errs)*1000:+7.0f}" if errs else "      -"
        flag = "<<" if errs and abs(np.median(errs)) > 0.25 else ("?" if len(errs) < 0.6 * len(idx) else "")
        rtxt = " ".join(rw[k].token for k in idx)
        htxt = " ".join(hw[pairs[k]].token if k in pairs else "_" for k in idx)
        print(f"{rw[idx[0]].start:7.2f} {e} {len(errs):2}/{len(idx):2} {flag:2} {rtxt[:48]:48} | {htxt[:48]}")
    # unmatched hyp words
    used = set(pairs.values())
    extra = [hw[j].token for j in range(len(hw)) if j not in used]
    print(f"\nhyp words not in ref ({len(extra)}): {' '.join(extra[:80])}")


if __name__ == "__main__":
    main(*sys.argv[1:])
