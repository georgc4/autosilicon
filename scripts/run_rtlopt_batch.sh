#!/usr/bin/env bash
# Run RTL-OPT batch experiments.
# Usage: ./scripts/run_rtlopt_batch.sh [--mode iterative|best-of-n] [--n 10] [--designs alu_64bit fsm ...]
# Ctrl-C kills everything cleanly.

set -euo pipefail

cleanup() {
    echo ""
    echo "Cleaning up..."
    kill 0 2>/dev/null || true
    stty sane 2>/dev/null || true
    exit 130
}

trap cleanup INT QUIT TERM

cd "$(dirname "$0")/.."
python3 benchmarks/rtl-opt/batch_run.py "$@"
