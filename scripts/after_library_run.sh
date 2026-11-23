#!/usr/bin/env bash
# Runs once the unattended library run has ended: re-score every song with the current code
# and retrain the issue-risk model on all of them.  Results: out/library/final_report.txt,
# out/library/retrain.log; the previous model is kept as ultraai/issue_model.prev.pkl.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
rm -rf out/library/runs/final out/library/runs/final_bench
$PY -u scripts/regen_library.py final m2,m3 library > out/library/runs/final.log 2>&1
$PY -u scripts/regen_library.py final_bench m3 songs > out/library/runs/final_bench.log 2>&1
cp -f ultraai/issue_model.pkl ultraai/issue_model.prev.pkl 2>/dev/null
ULTRAAI_ERR_S=0.5 $PY scripts/issue_calibrate.py --save final:library final_bench:songs > out/library/retrain.log 2>&1
{
  echo "== library status"; $PY scripts/library_status.py
  echo; echo "== M2 vs M3 (same songs, current code)"; $PY scripts/compare_runs.py final:m2 final:m3
  echo; echo "== old M2 (day 1 code) vs current M3"; $PY scripts/compare_runs.py base:m2 final:m3 | head -8
  echo; echo "== issue model"; grep -v '^skip' out/library/retrain.log
} > out/library/final_report.txt 2>&1
echo "after-run done $(date -u)" >> out/library/progress.log
