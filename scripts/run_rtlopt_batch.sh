#!/usr/bin/env bash
# Run RTL-OPT batch experiments.
# Usage: ./scripts/run_rtlopt_batch.sh [--mode iterative|best-of-n] [--n 10] [--designs alu_64bit fsm ...]
# Ctrl-C kills everything cleanly.

set -euo pipefail
trap 'echo "Interrupted."; kill 0; stty sane; exit 130' INT QUIT TERM

cd "$(dirname "$0")/.."
exec python3 benchmarks/rtl-opt/batch_run.py "$@"
