#!/usr/bin/env bash
# Background research queue: transcript variants on the 25 depth songs, then grow the analysed
# benchmark set with the not-yet-cached library songs (scripts/bench_cache_fill.py).
cd "$(dirname "$0")/.."
BENCH_SONGS=out/bench-depth/songs.txt \
BENCH_CONFIGS="asrsep:UVR-MDX-NET-Voc_FT.onnx,asrsep:melband,asr:large-v3" \
  .venv/bin/python -u scripts/bench_models.py out/bench-depth >> out/bench-depth/bench.log 2>&1
mkdir -p out/cache-fill && cd out/cache-fill && \
  ../../.venv/bin/python -u ../../scripts/bench_cache_fill.py ../../library 120 >> fill.log 2>&1
