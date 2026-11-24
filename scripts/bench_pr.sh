#!/usr/bin/env bash
# Quick benchmark gate for a pipeline PR — run ON THE processing server:
#
#   bash scripts/bench_pr.sh <branch> [tag]
#
# Regenerates every cached library song with the m2/m3 generators from <branch>
# and compares against the base run (BASE_REF, default main; base tag "base").
# Uses a separate git worktree + symlinks so a benchmark/library job running in
# ~/UltraAI is not disturbed, while sharing ~/.cache/ultraai (the expensive cache).
# Runs at CPUWeight=10 inside ultraai.slice so it never starves real work.
#
# Gate: no regression vs base on onset_within_250ms / onset_rel_within_100ms /
# pitch_class_acc in the compare_runs table. Paste the table into the PR.
set -euo pipefail

BRANCH="${1:?usage: bench_pr.sh <branch> [tag]}"
TAG="${2:-$(echo "$BRANCH" | sed 's|^pr/||; s|/|-|g')}"
BASE_REF="${BASE_REF:-main}"
BASE_TAG="${BASE_TAG:-base}"
REPO="$HOME/UltraAI"

git -C "$REPO" fetch origin

setup() {  # setup <worktree-dir> <ref>
    local wt="$1" ref="$2"
    git -C "$REPO" worktree add --force "$wt" "$ref" 2>/dev/null || git -C "$REPO" worktree add --force "$wt" "origin/$ref"
    cd "$wt"
    ln -sfn "$REPO/.venv" .venv
    [ -e "$REPO/library" ] && ln -sfn "$REPO/library" library
    [ -e "$REPO/songs" ] && ln -sfn "$REPO/songs" songs
    ln -sfn "$REPO/out" out
}

run() {  # run <tag>
    systemd-run --user --slice=ultraai.slice --wait --pipe \
        -p WorkingDirectory="$PWD" -p CPUWeight=10 \
        .venv/bin/python -u scripts/regen_library.py "$1" m2,m3
}

setup "$HOME/bench-$BASE_TAG" "$BASE_REF"
if [ ! -s "out/library/runs/$BASE_TAG/results.jsonl" ]; then
    echo "== base run ($BASE_TAG from $BASE_REF) =="
    run "$BASE_TAG"
fi

setup "$HOME/bench-$TAG" "$BRANCH"
echo "== PR run ($TAG from $BRANCH) =="
run "$TAG"

echo "== m2: $BASE_TAG vs $TAG =="
.venv/bin/python scripts/compare_runs.py "$BASE_TAG:m2" "$TAG:m2"
echo "== m3: $BASE_TAG vs $TAG =="
.venv/bin/python scripts/compare_runs.py "$BASE_TAG:m3" "$TAG:m3"
