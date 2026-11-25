"""Where are the remaining points lost?  Ceiling analysis on the cached library songs.

    python scripts/ceiling.py [run=integ] [library] [out/library]

Pitch (frame-wise pitch class, the same frames as ``pitch_class_acc``):
  track    the raw pitch curve (HTDemucs vocals, SwiftF0) vs the reference notes:
           how often the *curve itself* has the reference's pitch class
  oracle   reference notes (their timing) with the curve's median pitch per note:
           what perfect note placement + splitting would score with this pitch curve
  ours     the generated song (run ``run``), i.e. pitch_class_acc
  Each also with the best constant transposition per song (reference charts are
  sometimes transposed), so a metric artefact shows up separately.

Timing: generated words split into right (<= 250 ms), late/early (matched, > 250 ms) and
unmatched (word not in the reference there: lyrics / structure), plus the reference words we
never produced (missing).

Writes out/analysis/ceiling.json (per song) and prints the summary.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.compare_runs import ref_shifted  # noqa: E402
from ultraai.audio import work_dir  # noqa: E402
from ultraai.eval.harness import load_cases  # noqa: E402
from ultraai.eval.metrics import FRAME, match_words, timed_words  # noqa: E402
from ultraai.ultrastar.reader import read  # noqa: E402

ERR_S = 0.25
MIN_CONF = 0.5


def ref_frames(song) -> dict[int, int]:
    out = {}
    for n in song.notes(1):
        if not n.is_pitched:
            continue
        for f in range(round(song.beat_to_seconds(n.start) / FRAME), round(song.beat_to_seconds(n.end) / FRAME)):
            out[f] = n.pitch
    return out


def curve(audio: Path, sep="htdemucs"):
    p = work_dir(audio) / f"vocals.{sep}.pitch.npz"
    if not p.exists():
        return None
    z = np.load(p)
    t, m, c = z["times"], z["midi"], z["confidence"]
    ok = np.isfinite(m) & (c >= MIN_CONF)
    return t[ok], m[ok]


def curve_frames(t, m) -> dict[int, float]:
    out: dict[int, list] = {}
    for ti, mi in zip(t, m):
        out.setdefault(int(round(ti / FRAME)), []).append(mi)
    return {f: float(np.median(v)) for f, v in out.items()}


def pc_acc(pairs: list[tuple[float, int]], shift: int = 0) -> tuple[float, float]:
    """(exact, within 1 semitone) pitch-class accuracy of (hyp midi, ref midi) pairs."""
    if not pairs:
        return float("nan"), float("nan")
    d = [abs(((round(h) + shift - r) + 6) % 12 - 6) for h, r in pairs]
    return sum(x == 0 for x in d) / len(d), sum(x <= 1 for x in d) / len(d)


def best_shift(pairs) -> int:
    return max(range(-6, 6), key=lambda s: (pc_acc(pairs, s)[0], -abs(s)))


def oracle_pairs(ref, cf: dict[int, float]) -> list[tuple[float, int]]:
    """Reference notes with the curve's median pitch inside each note, on the note's frames."""
    out = []
    for n in ref.notes(1):
        if not n.is_pitched:
            continue
        fs = range(round(ref.beat_to_seconds(n.start) / FRAME), round(ref.beat_to_seconds(n.end) / FRAME))
        vals = [cf[f] for f in fs if f in cf]
        if not vals:
            continue
        med = float(np.median(vals))
        out += [(med, n.pitch) for f in fs if f in cf]  # same frames the curve covers
    return out


def main(run="integ", root="library", out="out/library"):
    d = Path(out) / "runs" / run
    metrics = {}
    for ln in (d / "results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(ln)
        if isinstance(r.get("m3"), dict) and "error" not in r["m3"]:
            metrics[r["name"]] = r["m3"]
    songs = []
    for case in load_cases(root):
        hyp_p = d / "m3" / f"{case.name}.txt"
        if case.name not in metrics or not hyp_p.exists() or ref_shifted(metrics[case.name]):
            continue
        if len(case.reference.voices) > 1 or case.audio is None:
            continue
        cv = curve(case.audio)
        if cv is None:
            continue
        ref, hyp = case.reference, read(hyp_p)
        rf, hf, cf = ref_frames(ref), ref_frames(hyp), curve_frames(*cv)
        track = [(cf[f], rf[f]) for f in rf if f in cf]
        oracle = oracle_pairs(ref, cf)
        ours = [(float(hf[f]), rf[f]) for f in rf if f in hf]
        row = {"song": case.name, "ref_frames": len(rf),
               "track_cover": len(track) / max(1, len(rf)), "ours_cover": len(ours) / max(1, len(rf))}
        # the same frames for all three: reference note, confident curve and our note all present
        onote = {}
        for n in ref.notes(1):
            if not n.is_pitched:
                continue
            fs = range(round(ref.beat_to_seconds(n.start) / FRAME), round(ref.beat_to_seconds(n.end) / FRAME))
            vals = [cf[f] for f in fs if f in cf]
            if vals:
                med = float(np.median(vals))
                onote.update({f: med for f in fs})
        common = [f for f in rf if f in cf and f in hf and f in onote]
        row["common_frames"] = len(common)
        for name, pairs in (("c_track", [(cf[f], rf[f]) for f in common]),
                            ("c_oracle", [(onote[f], rf[f]) for f in common]),
                            ("c_ours", [(float(hf[f]), rf[f]) for f in common])):
            row[name], row[name + "_1"] = pc_acc(pairs)
        for name, pairs in (("track", track), ("oracle", oracle), ("ours", ours)):
            s = best_shift(pairs) if pairs else 0
            row[name], row[name + "_1"] = pc_acc(pairs)
            row[name + "_shift"], row[name + "_best"] = s, pc_acc(pairs, s)[0]
        # timing breakdown
        rw, hw = timed_words(ref), timed_words(hyp)
        pairs = match_words(rw, hw)
        byh = {h: r for r, h in pairs}
        c = Counter()
        errs = []
        for i, w in enumerate(hw):
            r = byh.get(i)
            if r is None:
                c["unmatched"] += 1
            else:
                e = abs(w.start - rw[r].start)
                errs.append(e)
                c["right" if e <= ERR_S else "off"] += 1
        c["missing"] = len(rw) - len(pairs)
        row.update({f"w_{k}": v for k, v in c.items()}, hyp_words=len(hw), ref_words=len(rw),
                   off_median_ms=round(1000 * st.median([e for e in errs if e > ERR_S]), 0) if c["off"] else None)
        songs.append(row)
        print(f"{case.name[:45]:45} track {row['track']:.2f} oracle {row['oracle']:.2f} ours {row['ours']:.2f} "
              f"shift {row['oracle_shift']:+d}  words right {c['right']} off {c['off']} unmatched {c['unmatched']} "
              f"missing {c['missing']}", flush=True)
    od = Path("out/analysis")
    od.mkdir(parents=True, exist_ok=True)
    (od / "ceiling.json").write_text(json.dumps(songs, ensure_ascii=False, indent=1))
    summary(songs)


def summary(songs):
    def mean(k):
        v = [s[k] for s in songs if s.get(k) is not None and s[k] == s[k]]
        return 100 * st.mean(v) if v else float("nan")

    print(f"\n== {len(songs)} songs (shifted references and duets left out)")
    print("pitch class (frame-wise, like pitch_class_acc)      exact   ±1   with best transposition")
    for k, label in (("track", "pitch curve itself vs reference"), ("oracle", "reference notes + curve pitch (ceiling)"),
                     ("ours", "our generated notes")):
        print(f"  {label:45} {mean(k):5.1f} {mean(k + '_1'):5.1f}   {mean(k + '_best'):5.1f}")
    print("  same frames for all three (reference note + confident curve + our note):")
    for k, label in (("c_track", "pitch curve itself"), ("c_oracle", "reference notes + curve pitch"),
                     ("c_ours", "our generated notes")):
        print(f"    {label:43} {mean(k):5.1f} {mean(k + '_1'):5.1f}")
    print(f"  reference frames covered by the curve: {mean('track_cover'):.1f}%, by our notes: {mean('ours_cover'):.1f}%")
    shifted = [s for s in songs if s["oracle_shift"] != 0 and s["oracle_best"] - s["oracle"] > 0.2]
    print(f"  songs whose reference looks transposed (oracle gains > 20 points by shifting): {len(shifted)}"
          + (": " + ", ".join(f"{s['song'][:30]} ({s['oracle_shift']:+d})" for s in shifted[:12]) if shifted else ""))
    tot = Counter()
    for s in songs:
        for k in ("w_right", "w_off", "w_unmatched", "w_missing"):
            tot[k] += s.get(k, 0)
    hyp = tot["w_right"] + tot["w_off"] + tot["w_unmatched"]
    print("\ntiming (generated words)")
    print(f"  right (<= 250 ms) {100 * tot['w_right'] / hyp:.1f}%   off (> 250 ms) {100 * tot['w_off'] / hyp:.1f}%   "
          f"not in the reference there (lyrics/structure) {100 * tot['w_unmatched'] / hyp:.1f}%")
    print(f"  reference words we never produced: {tot['w_missing']} ({100 * tot['w_missing'] / (tot['w_right'] + tot['w_off'] + tot['w_missing']):.1f}% of the reference)")
    offs = [s["off_median_ms"] for s in songs if s.get("off_median_ms")]
    if offs:
        print(f"  median miss of the 'off' words: {st.median(offs):.0f} ms (per-song medians)")


if __name__ == "__main__":
    main(*sys.argv[1:])
