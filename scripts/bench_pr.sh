#!/usr/bin/env bash
# Quick benchmark gate for a pipeline PR — run inside any UltraAI checkout that has
# the benchmark library and the analysis cache (any machine that has both):
#
#   bash scripts/bench_pr.sh <branch> [tag]
#
# Regenerates every cached library song with the m2/m3 generators from <branch> and
# compares against the base run (BASE_REF, default main; base tag "base").
# Uses a separate git worktree so the main checkout (and any running job) is not
# disturbed; shares ULTRAAI_CACHE (default ~/.cache/ultraai).
# On Linux servers it runs inside ultraai.slice at CPUWeight=10 so it never starves
# real work; elsewhere it runs directly.
#
# Gate: no regression vs base on onset_within_250ms / onset_rel_within_100ms /
# pitch_class_acc in the compare_runs table. Paste the table into the PR.
#
# Env overrides: REPO (checkout), LIBRARY (benchmark songs, default $REPO/library),
# OUT (results, default $REPO/out), VENV (python env with .[worker], default
# $REPO/.venv), BASE_REF / BASE_TAG.
set -euo pipefail

BRANCH="${1:?usage: bench_pr.sh <branch> [tag]}"
TAG="${2:-$(echo "$BRANCH" | sed 's|^pr/||; s|/|-|g')}"
REPO="${REPO:-$(git rev-parse --show-toplevel)}"
LIBRARY="${LIBRARY:-$REPO/library}"
OUT="${OUT:-$REPO/out}"
VENV="${VENV:-$REPO/.venv}"
BASE_REF="${BASE_REF:-main}"
BASE_TAG="${BASE_TAG:-base}"

[ -d "$LIBRARY" ] || { echo "no benchmark library at $LIBRARY (set LIBRARY=...)" >&2; exit 2; }
if [ -x "$VENV/bin/pip" ]; then PIP="$VENV/bin/pip"; PY="$VENV/bin/python";
else PIP="$VENV/Scripts/pip.exe"; PY="$VENV/Scripts/python.exe"; fi
[ -x "$PY" ] || { echo "no python env at $VENV (set VENV=...; needs .[worker])" >&2; exit 2; }

git -C "$REPO" fetch origin 2>/dev/null || true

setup() {  # setup <worktree> <ref>
    local wt="$1" ref="$2"
    rm -rf "$wt"
    git -C "$REPO" worktree add --force "$wt" "origin/$ref" 2>/dev/null \
        || git -C "$REPO" worktree add --force "$wt" "$ref"
    cd "$wt"
    "$PIP" install -q -e . --no-deps   # repoint the shared env at this worktree
}

run() {  # run <tag>
    if command -v systemd-run >/dev/null 2>&1; then
        systemd-run --user --slice=ultraai.slice --wait --pipe \
            -p WorkingDirectory="$PWD" -p CPUWeight=10 \
            "$PY" -u scripts/regen_library.py "$1" m2,m3 "$LIBRARY" "$OUT"
    else
        "$PY" -u scripts/regen_library.py "$1" m2,m3 "$LIBRARY" "$OUT"
    fi
}

setup "$REPO/../bench-$BASE_TAG" "$BASE_REF"
if [ ! -s "$OUT/library/runs/$BASE_TAG/results.jsonl" ]; then
    echo "== base run ($BASE_TAG from $BASE_REF) =="
    run "$BASE_TAG"
fi

setup "$REPO/../bench-$TAG" "$BRANCH"
echo "== PR run ($TAG from $BRANCH) =="
run "$TAG"

export ULTRAAI_OUT="$OUT/library"
echo "== m2: $BASE_TAG vs $TAG =="
"$PY" scripts/compare_runs.py "$BASE_TAG:m2" "$TAG:m2"
echo "== m3: $BASE_TAG vs $TAG =="
"$PY" scripts/compare_runs.py "$BASE_TAG:m3" "$TAG:m3"
