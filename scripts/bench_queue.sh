#!/usr/bin/env bash
# Server benchmark queue: the remaining model configs on the 3 songs, then the separation
# depth pass (HTDemucs baseline + top-3 separations) on 25 songs (out/bench-depth/songs.txt).
cd "$(dirname "$0")/.."
.venv/bin/python -u scripts/bench_models.py out/bench >> out/bench.log 2>&1
BENCH_SONGS=out/bench-depth/songs.txt \
BENCH_CONFIGS="sep:htdemucs,sep:Kim_Vocal_2.onnx,sep:UVR-MDX-NET-Voc_FT.onnx,sep:melband" \
  .venv/bin/python -u scripts/bench_models.py out/bench-depth >> out/bench-depth/bench.log 2>&1
