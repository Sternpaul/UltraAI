"""Do double-check warnings point at real errors?

    python scripts/doublecheck_eval.py [songs.txt=out/bench-depth/songs.txt] [out=out/analysis/dcheck]

Generates every song (m3, HTDemucs + SwiftF0) once per variant: no double check, one extra
separation model, one extra pitch model, or both.  The issues the pipeline writes are then
checked against the hand-made reference:

  problem  a generated word whose onset is > 250 ms off or that the reference does not have
           there, a note whose pitch class is wrong on most of its frames, or a note where the
           reference has no singing at all ("extra")
  per issue source (issue model / separation check / pitch check):
    issues per song, precision (issues that touch a problem), recall (problems touched),
    chance (the same issues placed at random in the song), and for the double checks the
    problems they catch that no issue-model issue touches ("new").

Resumable: per variant and song the issues + generated .txt go to OUT/<variant>/.
Summary: OUT/summary.txt.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.compare_runs import ref_shifted  # noqa: E402

BASE = {"ULTRAAI_SEPARATION": "htdemucs", "ULTRAAI_PITCH": "swift"}
VARIANTS = [
    ("none", {}),
    ("sep-vocft", {"ULTRAAI_SEP_CHECK": "UVR-MDX-NET-Voc_FT.onnx"}),
    ("sep-kim2", {"ULTRAAI_SEP_CHECK": "Kim_Vocal_2.onnx"}),
    ("sep-melband", {"ULTRAAI_SEP_CHECK": "melband"}),
    ("pitch-rmvpe", {"ULTRAAI_PITCH_CHECK": "rmvpe"}),
    ("pitch-fcpe", {"ULTRAAI_PITCH_CHECK": "fcpe"}),
    ("vocft+rmvpe", {"ULTRAAI_SEP_CHECK": "UVR-MDX-NET-Voc_FT.onnx", "ULTRAAI_PITCH_CHECK": "rmvpe"}),
]
if os.environ.get("DC_ONLY"):  # comma list of variant names, for quick tests
    VARIANTS = [v for v in VARIANTS if v[0] in os.environ["DC_ONLY"].split(",")]
ENV_KEYS = ("ULTRAAI_SEP_CHECK", "ULTRAAI_PITCH_CHECK", "ULTRAAI_SEP2", "ULTRAAI_PITCH2")
ERR_S = 0.25


def source(kind: str) -> str:
    return {"models_sep": "sep-check", "models_pitch": "pitch-check", "auto-fixed": "pitch-check"}.get(kind, "issue-model")


def generate(case, variant_env, outdir: Path) -> dict | None:
    """Issues + .txt for one song and variant (cached in outdir)."""
    import contextlib
    import io

    from ultraai.eval.harness import GENERATORS
    from ultraai.ultrastar.writer import write

    j = outdir / f"{case.name}.json"
    if j.exists():
        return json.loads(j.read_text(encoding="utf-8"))
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update({**BASE, **variant_env})
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            song = GENERATORS["m3"](case)
    except Exception as exc:  # noqa: BLE001
        print(f"   {case.name[:40]}: {type(exc).__name__}: {exc}", flush=True)
        return None
    outdir.mkdir(parents=True, exist_ok=True)
    write(song, outdir / f"{case.name}.txt")
    rec = {"issues": song.generation_info.get("issues", []), "seconds": round(time.time() - t0)}
    j.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


def problems(ref, hyp) -> tuple[list[tuple[float, float, str]], int]:
    """Problem spans (start, end, type) in the generated song, and its number of notes."""
    from ultraai.eval.metrics import FRAME, match_words, timed_words

    out = []
    rw, hw = timed_words(ref), timed_words(hyp)
    byh = {h: r for r, h in match_words(rw, hw)}
    for i, w in enumerate(hw):
        r = byh.get(i)
        if r is None:
            out.append((w.start, w.end, "lyrics"))
        elif abs(w.start - rw[r].start) > ERR_S:
            out.append((w.start, w.end, "timing"))
    rf, act = {}, set()
    for n in ref.notes(1):
        fs = range(round(ref.beat_to_seconds(n.start) / FRAME), round(ref.beat_to_seconds(n.end) / FRAME))
        act.update(fs)
        if n.is_pitched:
            rf.update({f: n.pitch for f in fs})
    notes = 0
    for n in hyp.notes(1):
        if not n.is_pitched:
            continue
        notes += 1
        a, b = hyp.beat_to_seconds(n.start), hyp.beat_to_seconds(n.end)
        fs = range(round(a / FRAME), round(b / FRAME))
        if not any(f in act for f in fs):
            out.append((a, b, "extra"))
            continue
        common = [f for f in fs if f in rf]
        if len(common) >= 4:
            wrong = sum(((n.pitch - rf[f]) % 12) != 0 for f in common)
            if wrong > len(common) / 2:
                out.append((a, b, "pitch"))
    return out, notes


def overlaps(a0, a1, b0, b1, pad=0.05) -> bool:
    return a0 < b1 + pad and b0 < a1 + pad


def evaluate(cases, outroot: Path) -> str:
    import random

    from ultraai.ultrastar.reader import read

    rng = random.Random(7)
    lines = []
    base_rate = {}
    for vname, _ in VARIANTS:
        vdir = outroot / vname
        stats = defaultdict(lambda: defaultdict(float))
        n_songs = 0
        new_by_type = defaultdict(lambda: defaultdict(int))
        for case in cases:
            j = vdir / f"{case.name}.json"
            if not j.exists():
                continue
            n_songs += 1
            rec = json.loads(j.read_text(encoding="utf-8"))
            probs, notes = problems(case.reference, read(vdir / f"{case.name}.txt"))
            base_rate.setdefault(case.name, len(probs) / max(1, notes + len(probs)))
            by_src = defaultdict(list)
            for i in rec["issues"]:
                if i.get("start") is not None and i.get("end") is not None:
                    by_src[source(i["kind"])].append(i)
            model_hit = {k for k, p in enumerate(probs)
                         if any(overlaps(p[0], p[1], i["start"], i["end"]) for i in by_src["issue-model"])}
            song_end = max([p[1] for p in probs] + [i["end"] for v in by_src.values() for i in v] + [1.0])
            for src, iss in by_src.items():
                hit = sum(any(overlaps(p[0], p[1], i["start"], i["end"]) for p in probs) for i in iss)
                # chance: the same issues at random places in the song (20 draws each)
                for i in iss:
                    d = max(0.0, i["end"] - i["start"])
                    for _ in range(20):
                        a = rng.uniform(0, max(0.0, song_end - d))
                        stats[src]["chance"] += any(overlaps(p[0], p[1], a, a + d) for p in probs) / 20
                caught = {k for k, p in enumerate(probs) if any(overlaps(p[0], p[1], i["start"], i["end"]) for i in iss)}
                st = stats[src]
                st["issues"] += len(iss)
                st["hit"] += hit
                st["caught"] += len(caught)
                st["new"] += len(caught - model_hit)
                for k in caught - model_hit:
                    new_by_type[src][probs[k][2]] += 1
            stats["_all"]["problems"] += len(probs)
            stats["_all"]["notes"] += notes
        if not n_songs:
            continue
        P = stats["_all"]["problems"]
        lines.append(f"\n== {vname}: {n_songs} songs, {int(P)} problems "
                     f"({100 * P / max(1, P + stats['_all']['notes']):.0f}% of words+notes)")
        lines.append(f"  {'source':12} {'issues/song':>11} {'precision':>9} {'chance':>7} {'recall':>7} {'new':>5}  new by type")
        for src in ("issue-model", "sep-check", "pitch-check"):
            st = stats.get(src)
            if not st or not st["issues"]:
                continue
            nb = ", ".join(f"{t} {c}" for t, c in sorted(new_by_type[src].items())) if src != "issue-model" else ""
            lines.append(f"  {src:12} {st['issues'] / n_songs:11.1f} {100 * st['hit'] / st['issues']:8.0f}% "
                         f"{100 * st['chance'] / st['issues']:6.0f}% "
                         f"{100 * st['caught'] / max(1, P):6.0f}% {int(st['new']) if src != 'issue-model' else '':>5}  {nb}")
    return "\n".join(lines)


def main(songs_file="out/bench-depth/songs.txt", out="out/analysis/dcheck"):
    from ultraai.eval.harness import load_cases

    names = [s.strip() for s in Path(songs_file).read_text(encoding="utf-8").splitlines() if s.strip()]
    lib = {c.name: c for c in load_cases("library")}
    shifted = set()
    for ln in Path("out/library/runs/integ/results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if isinstance(r.get("m3"), dict) and ref_shifted(r["m3"]):
            shifted.add(r["name"])
    cases = [lib[n] for n in names if n in lib and n not in shifted and len(lib[n].reference.voices) == 1]
    outroot = Path(out)
    print(f"{len(cases)} songs x {len(VARIANTS)} variants", flush=True)
    for vname, env in VARIANTS:
        for k, case in enumerate(cases, 1):
            t0 = time.time()
            rec = generate(case, env, outroot / vname)
            if rec is not None:
                print(f"{time.strftime('%H:%M')} {vname:12} [{k}/{len(cases)}] {case.name[:40]:40} "
                      f"{len(rec['issues'])} issues  {time.time() - t0:.0f} s", flush=True)
    report = evaluate(cases, outroot)
    (outroot / "summary.txt").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main(*sys.argv[1:])
