#!/usr/bin/env bash
# Run PicoRV32 iterative optimization.
# Usage: ./scripts/run_picorv32.sh [--run-name opus-iter-r1] [--max-experiments 10] [--agent-timeout 1800]
# Ctrl-C kills everything cleanly and removes worktrees.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESIGN=picorv32
WORKTREE_DIR="$REPO_ROOT/.autosilicon-worktrees/$DESIGN"

cleanup() {
    echo ""
    echo "Cleaning up..."
    kill 0 2>/dev/null || true

    # Remove worktrees for this design
    for wt in "$WORKTREE_DIR"/*/; do
        [ -d "$wt" ] || continue
        branch=$(git -C "$wt" branch --show-current 2>/dev/null || true)
        git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null || true
        [ -n "$branch" ] && git -C "$REPO_ROOT" branch -D "$branch" 2>/dev/null || true
    done
    rmdir "$WORKTREE_DIR" 2>/dev/null || true

    stty sane 2>/dev/null || true
    exit 130
}

trap cleanup INT QUIT TERM

cd "$REPO_ROOT"
python3 scripts/launch_agent_run.py \
    --design-dir "designs/$DESIGN" \
    --agent-cli claude \
    --agent-model claude-opus-4-6 \
    --seed-ref HEAD \
    "$@"
