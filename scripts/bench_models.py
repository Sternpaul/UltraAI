"""Benchmark every pitch / separation / backing-vocal model on a few library songs.

    python scripts/bench_models.py [out/bench]

Each config = environment for the pipeline (main separation, pitch model, lead model).
All configs use the same Whisper transcript (from HTDemucs vocals) so only the model's
effect on timing and notes is measured.  Resumable: finished (config, song) pairs are
skipped.  Results: out/bench/results.jsonl; summary: scripts/bench_report.py.

Env: BENCH_ONLY (substring of config names), BENCH_CONFIGS (comma list of exact config
names; "sep:htdemucs" = baseline), BENCH_SONGS (file with one song name per line).
"""
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

SONGS = ["Charli XCX - Apple", "Creedence Clearwater Revival - Have You Ever Seen The Rain",
         "Wir sind Helden - Denkmal"]

BASE = {"ULTRAAI_SEPARATION": "htdemucs", "ULTRAAI_PITCH": "swift", "ULTRAAI_ASR_SEP": "htdemucs",
        "ULTRAAI_PITCH_CHECK": ""}  # double checks only add warnings
CONFIGS = [  # (name, kind, env overrides) - cheap ones first
    ("pitch:swift", "pitch", {}),
    ("pitch:fcpe", "pitch", {"ULTRAAI_PITCH": "fcpe"}),
    ("pitch:pesto", "pitch", {"ULTRAAI_PITCH": "pesto"}),
    ("pitch:crepe-tiny", "pitch", {"ULTRAAI_PITCH": "crepe-tiny"}),
    ("pitch:rmvpe", "pitch", {"ULTRAAI_PITCH": "rmvpe"}),
    ("pitch:vote", "pitch", {"ULTRAAI_PITCH": "vote"}),
    ("pitch:crepe-full", "pitch", {"ULTRAAI_PITCH": "crepe-full"}),
]
SEPARATIONS = ["hdemucs_mmi.yaml", "UVR-MDX-NET-Voc_FT.onnx", "Kim_Vocal_2.onnx", "UVR-MDX-NET_Main_406.onnx",
               "htdemucs_ft", "MDX23C-8KFFT-InstVoc_HQ.ckpt", "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt",
               "melband", "melband_roformer_big_beta4.ckpt", "melband_roformer_big_beta5e.ckpt",
               "mel_band_roformer_kim_ft_unwa.ckpt", "MelBandRoformerBigSYHFTV1.ckpt",
               "model_bs_roformer_ep_368_sdr_12.9628.ckpt",
               "melband_roformer_instvox_duality_v2.ckpt"]
CONFIGS += [(f"sep:{m}", "separation", {"ULTRAAI_SEPARATION": m}) for m in SEPARATIONS]
LEADS = []  # backing-vocal removal measured on 25 songs: timing -1.5 (see BENCHMARK.md), not ranked further
# dropped to save time (near-duplicates of measured ones): BS-RoFormer ep_317
CONFIGS += [(f"lead:{m}", "lead", {"ULTRAAI_LEAD": m}) for m in LEADS]
# transcript: a bigger Whisper model, or Whisper listening to another separation's vocals
CONFIGS += [("asr:large-v3", "asr", {"ULTRAAI_ASR_MODEL": "large-v3"}),
            ("asrsep:UVR-MDX-NET-Voc_FT.onnx", "asr", {"ULTRAAI_ASR_SEP": "UVR-MDX-NET-Voc_FT.onnx"}),
            ("asrsep:melband", "asr", {"ULTRAAI_ASR_SEP": "melband"})]


def main(out="out/bench"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    res = out / "results.jsonl"
    done = set()
    if res.exists():
        done = {(json.loads(ln)["config"], json.loads(ln)["song"]) for ln in res.read_text().splitlines() if ln.strip()}
    only = os.environ.get("BENCH_ONLY")
    exact = [c for c in os.environ.get("BENCH_CONFIGS", "").split(",") if c]
    songs = SONGS
    if os.environ.get("BENCH_SONGS"):  # file with one song name per line
        songs = [ln.strip() for ln in Path(os.environ["BENCH_SONGS"]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    configs = CONFIGS + [("sep:htdemucs", "separation", {})]  # baseline, also for other song sets
    for name, kind, env in configs:
        if only and only not in name:
            continue
        if exact and name not in exact:
            continue
        if not exact and name == "sep:htdemucs":
            continue  # the main run's baseline is pitch:swift
        for song in songs:
            if (name, song) in done:
                continue
            for k, v in {**BASE, **env}.items():
                os.environ[k] = v
            os.environ["ULTRAAI_STAGE_TIMING"] = "1"  # per-stage timings into each row
            for k in {k for _, _, e in configs for k in e} - set(BASE):  # no leftovers from earlier configs
                if k not in env:
                    os.environ.pop(k, None)
            from ultraai import generate
            from ultraai.eval.harness import GENERATORS, load_cases
            from ultraai.eval.metrics import compare

            generate.reset_stage_times()
            case = next(c for c in load_cases("library") if c.name == song)
            t0 = time.time()
            row = {"config": name, "kind": kind, "song": song}
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    hyp = GENERATORS["m3"](case)
                row["metrics"] = {k: round(v, 4) for k, v in compare(case.reference, hyp).items()
                                  if isinstance(v, float)}
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {exc}"
            row["seconds"] = round(time.time() - t0)
            row["stages"] = {k: round(v, 1) for k, v in generate.STAGE_TIMES.items()}
            with open(res, "a") as f:
                f.write(json.dumps(row) + "\n")
            m = row.get("metrics", {})
            print(f"{time.strftime('%H:%M')} {name[:48]:48} {song[:28]:28} {row['seconds']:5d}s "
                  f"w250 {m.get('onset_within_250ms', float('nan')):.3f} pitch {m.get('pitch_class_acc', float('nan')):.3f} "
                  f"{row.get('error', '')}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
