#!/usr/bin/env bash
# Run PicoRV32 iterative optimization.
# Usage: ./scripts/run_picorv32.sh [--run-name opus-iter-r1] [--max-experiments 10] [--agent-timeout 1800]
# Ctrl-C kills everything cleanly.

set -euo pipefail
trap 'echo "Interrupted."; kill 0; stty sane; exit 130' INT QUIT TERM

cd "$(dirname "$0")/.."
exec python3 scripts/launch_agent_run.py \
    --design-dir designs/picorv32 \
    --agent-cli claude \
    --agent-model claude-opus-4-6 \
    --seed-ref HEAD \
    "$@"
