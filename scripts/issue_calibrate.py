"""Fit + evaluate the issue risk model on songs with hand-made references.

    python scripts/issue_calibrate.py [--save] RUN:ROOT [RUN:ROOT ...]
    e.g.  issue_calibrate.py --save feat:library feat_bench:songs

A generated word is *wrong* if the reference has no such word there (text/structure error)
or its onset is > 250 ms off.  A screen line needs review if >= 1/3 of its words are wrong.
Evaluation is song-grouped cross-validation: how many wrong lines are flagged for a given
share of flagged lines.  --save fits on everything and writes ultraai/issue_model.json.
"""
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from compare_runs import ref_shifted  # noqa: E402

from ultraai import issues as iss  # noqa: E402
from ultraai.eval.harness import load_cases  # noqa: E402
from ultraai.eval.metrics import match_words, timed_words  # noqa: E402
from ultraai.ultrastar.reader import read  # noqa: E402

ERR_S = float(__import__("os").environ.get("ULTRAAI_ERR_S", "0.25"))


def collect(run: str, root: str, out="out/library"):
    d = Path(out) / "runs" / run
    metrics = {}
    for ln in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if isinstance(r.get("m3"), dict) and "error" not in r["m3"]:
            metrics[r["name"]] = r["m3"]
    rows = []
    for case in load_cases(root):
        info_p = d / "m3" / f"{case.name}.ultraai.json"
        if case.name not in metrics or not info_p.exists() or ref_shifted(metrics[case.name]):
            continue
        if len(case.reference.voices) > 1:  # duets: reference voice split is a different problem
            continue
        info = json.loads(info_p.read_text(encoding="utf-8"))
        words = [w for w in info["words"] if iss._key(w["text"])]
        hyp = timed_words(read(d / "m3" / f"{case.name}.txt"))
        ref = timed_words(case.reference)
        if len(hyp) != len(words):
            print(f"skip {case.name}: {len(hyp)} txt words vs {len(words)} feature words")
            continue
        pairs = dict((h, r) for r, h in match_words(ref, hyp))
        for i, w in enumerate(words):
            r = pairs.get(i)
            err = r is None or abs(hyp[i].start - ref[r].start) > ERR_S
            rows.append((f"{run}/{case.name}", w["line"], iss._vector(w["f"]), int(err), w["f"]))
    return rows


def fit(X, y):
    """Gradient-boosted trees: +2 AUC points over logistic regression, better top precision."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                       min_samples_leaf=100, l2_regularization=1.0, random_state=0)
    return {"kind": "hgb", "estimator": m.fit(X, y), "features": iss.FEATURES}


def predict(model, X):
    return model["estimator"].predict_proba(X)[:, 1]


def line_table(songs, lines, risk, y):
    """Per (song, line): line risk (as in issues.line_risk) and whether the line is bad."""
    by = {}
    for s, li, r, e in zip(songs, lines, risk, y):
        by.setdefault((s, li), []).append((r, e))
    out = []
    for key, v in by.items():
        rs = sorted((r for r, _ in v), reverse=True)
        k = max(1, math.ceil(len(rs) / 3))
        bad = sum(e for _, e in v) >= max(1, len(v) / 3)
        out.append((key[0], sum(rs[:k]) / k, bad))
    return out


def report(tab, label):
    risks = np.array([r for _, r, _ in tab])
    bad = np.array([b for _, _, b in tab])
    print(f"\n{label}: {len(tab)} lines, {bad.mean():.1%} need review")
    print("  flag share -> wrong lines caught   (threshold)")
    for share in (0.1, 0.15, 0.2, 0.25, 0.3, 0.4):
        t = np.quantile(risks, 1 - share)
        flagged = risks >= t
        print(f"   {share:4.0%} -> {bad[flagged].sum() / max(1, bad.sum()):6.1%}  "
              f"(precision {bad[flagged].mean():5.1%}, t={t:.3f})")


def main(*args):
    save = "--save" in args
    rows = []
    for spec in [a for a in args if a != "--save"]:
        run, root = spec.split(":")
        rows += collect(run, root)
    songs = [r[0] for r in rows]
    lines = [r[1] for r in rows]
    X = np.array([r[2] for r in rows], dtype=float)
    y = np.array([r[3] for r in rows])
    uniq = sorted(set(songs))
    print(f"{len(uniq)} songs, {len(rows)} words, {y.mean():.1%} wrong")
    # song-grouped 5-fold CV
    rng = np.random.default_rng(0)
    fold_of = {s: k % 5 for k, s in enumerate(rng.permutation(uniq))}
    folds = np.array([fold_of[s] for s in songs])
    risk = np.zeros(len(y))
    for k in range(5):
        tr, te = folds != k, folds == k
        risk[te] = predict(fit(X[tr], y[tr]), X[te])
    from sklearn.metrics import roc_auc_score

    print(f"word AUC (cross-validated): {roc_auc_score(y, risk):.3f}")
    report(line_table(songs, lines, risk, y), "model (cross-validated)")
    base = np.array([float(r[4]["ctc"] < 0.03 or r[4]["voiced"] < 0.3) for r in rows])  # old review rule
    report(line_table(songs, lines, base + 1e-6 * rng.random(len(base)), y), "old rule (ctc/voicing)")
    if save:
        import pickle

        model = fit(X, y)
        # threshold from the cross-validated risks (in-sample risks are too optimistic)
        tab = line_table(songs, lines, risk, y)
        model["threshold"] = float(np.quantile([r for _, r, _ in tab], 0.8))  # flag ~20 % of lines
        model["version"] = f"hgb-{len(uniq)}songs-err{int(ERR_S * 1000)}ms"
        iss.MODEL_PATH.write_bytes(pickle.dumps(model))
        print(f"saved {iss.MODEL_PATH} (threshold {model['threshold']:.3f})")


if __name__ == "__main__":
    main(*sys.argv[1:])
