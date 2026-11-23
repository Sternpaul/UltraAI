"""Break word-timing errors down by cause, for songs already analysed (cached).

Usage: python scripts/failure_analysis.py [hyp_dir] [songs_dir]
Re-runs alignment from cache to get per-word confidence, then reports per song:
bias, share of drift errors (> 1 s), errors for first-word-of-line vs inner words,
and whether low alignment confidence predicts big errors.
"""

import os
import statistics
import sys
from pathlib import Path

import numpy as np

from ultraai import align as aligner
from ultraai.eval.harness import load_cases
from ultraai.eval.metrics import match_words, norm_token, timed_words
from ultraai.generate import analyse, is_cached
from ultraai.lyrics import language_code, prepare


def main(songs="songs"):
    sep = os.environ.get("ULTRAAI_SEPARATION", "htdemucs")
    all_err, all_score = [], []
    print(f"{'song':34} {'n':>4} {'bias':>6} {'>250ms':>7} {'>1s':>6} {'1st-of-line':>11} {'inner':>6}")
    for case in load_cases(songs):
        if not case.audio or not is_cached(case.audio, sep):
            continue
        lyr = prepare(case.lyrics, language_code(case.metadata.get("LANGUAGE")))
        a = analyse(case.audio, sep)
        words = aligner.align(lyr, a.wave, em=a.emissions)
        hyp = [(norm_token(lyr.words[w.index].display), w.start, w.score,
                lyr.words[w.index].line) for w in words]
        hyp = [h for h in hyp if h[0]]
        ref = timed_words(case.reference)
        from types import SimpleNamespace as NS
        pairs = match_words(ref, [NS(token=h[0]) for h in hyp])
        errs = [(hyp[j][1] - ref[i].start, hyp[j][2], j == 0 or hyp[j][3] != hyp[j - 1][3]) for i, j in pairs]
        e = np.array([x[0] for x in errs])
        first = np.array([x[2] for x in errs])
        all_err += list(e)
        all_score += [x[1] for x in errs]
        print(f"{case.name[:34]:34} {len(e):4} {1000 * np.median(e):+6.0f} {np.mean(abs(e) > .25):7.1%} "
              f"{np.mean(abs(e) > 1):6.1%} {1000 * np.median(abs(e[first])):9.0f}ms {1000 * np.median(abs(e[~first])):4.0f}ms")
    e, s = np.abs(np.array(all_err)), np.array(all_score)
    print(f"\nALL {len(e)} words: median {1000 * np.median(e):.0f} ms, bias {1000 * statistics.median(all_err):+.0f} ms")
    for lo, hi in [(0, .1), (.1, .2), (.2, .4), (.4, .7), (.7, 1.01)]:
        m = (s >= lo) & (s < hi)
        if m.any():
            print(f"  confidence {lo:.1f}-{hi:.1f}: {m.sum():5} words  median err {1000 * np.median(e[m]):5.0f} ms"
                  f"  >250ms {np.mean(e[m] > .25):6.1%}  >1s {np.mean(e[m] > 1):6.1%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
