#!/usr/bin/env python3
"""Batch-run AutoSilicon on all RTL-OPT benchmark designs.

Runs iterative optimization on each rtlopt_* design and collects results
into a summary table.

Usage:
    # Iterative mode (N experiments per design):
    python3 benchmarks/rtl-opt/batch_run.py --mode iterative --n 10 \
        --agent-cli claude --agent-model claude-opus-4-6

    # Best-of-N mode (N independent one-shot attempts per design):
    python3 benchmarks/rtl-opt/batch_run.py --mode best-of-n --n 10 \
        --agent-cli claude --agent-model claude-opus-4-6

    # Dry-run to validate wiring:
    python3 benchmarks/rtl-opt/batch_run.py --mode iterative --n 1 --dry-run

    # Run a subset:
    python3 benchmarks/rtl-opt/batch_run.py --mode iterative --n 5 \
        --designs alu_64bit fsm divider_32bit
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESIGNS_DIR = REPO_ROOT / "designs"


def find_rtlopt_designs(subset: list[str] | None = None) -> list[Path]:
    """Find all rtlopt_* design directories."""
    designs = sorted(DESIGNS_DIR.glob("rtlopt_*"))
    if subset:
        designs = [d for d in designs if d.name.replace("rtlopt_", "") in subset]
    return designs


def run_iterative(design_dir: Path, n: int, args) -> int:
    """Run N iterative experiments on a single design."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "harness" / "autosilicon.py"),
        "--mode", "fe",
        "--design-dir", str(design_dir),
        "--agent-cli", args.agent_cli,
        "--agent-model", args.agent_model,
        "--max-experiments", str(n),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    return proc.returncode


def run_best_of_n(design_dir: Path, n: int, args) -> int:
    """Run N independent one-shot attempts on a single design."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_best_of_n.py"),
        "--design-dir", str(design_dir.relative_to(REPO_ROOT)),
        "--n", str(n),
        "--agent-cli", args.agent_cli,
        "--agent-model", args.agent_model,
        "--seed-ref", "HEAD",
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    return proc.returncode


def collect_baseline_results(designs: list[Path]) -> dict:
    """Collect baseline synthesis results for all designs."""
    baselines = {}
    for d in designs:
        name = d.name.replace("rtlopt_", "")
        csv_path = d / "synth" / "results" / "sweep_results.csv"
        if csv_path.exists():
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    baselines[name] = {
                        "cells": int(row.get("num_cells", 0)),
                    }
                    break
    return baselines


def main():
    parser = argparse.ArgumentParser(description="Batch-run AutoSilicon on RTL-OPT designs")
    parser.add_argument("--mode", choices=["iterative", "best-of-n"], required=True)
    parser.add_argument("--n", type=int, default=10, help="Number of experiments/attempts")
    parser.add_argument("--agent-cli", choices=["claude", "codex"], default="claude")
    parser.add_argument("--agent-model", default="claude-opus-4-6")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--designs", nargs="*", default=None,
                        help="Subset of design names to run (without rtlopt_ prefix)")
    args = parser.parse_args()

    designs = find_rtlopt_designs(args.designs)
    if not designs:
        print("No rtlopt_* designs found. Run benchmarks/rtl-opt/setup.py first.")
        return 1

    print(f"RTL-OPT Batch Run: {args.mode}, N={args.n}, {len(designs)} designs")
    print(f"Agent: {args.agent_cli} ({args.agent_model})")
    print(f"{'='*60}\n")

    results = []
    for i, design_dir in enumerate(designs, 1):
        name = design_dir.name.replace("rtlopt_", "")
        print(f"\n[{i}/{len(designs)}] {name}")
        print(f"{'-'*40}")

        if args.mode == "iterative":
            rc = run_iterative(design_dir, args.n, args)
        else:
            rc = run_best_of_n(design_dir, args.n, args)

        results.append({"design": name, "exit_code": rc})

    # Summary
    print(f"\n{'='*60}")
    print(f"Batch Summary")
    print(f"{'='*60}")
    ok = sum(1 for r in results if r["exit_code"] == 0)
    fail = sum(1 for r in results if r["exit_code"] != 0)
    print(f"  OK: {ok}, FAIL: {fail}")

    if fail > 0:
        print("  Failed designs:")
        for r in results:
            if r["exit_code"] != 0:
                print(f"    - {r['design']} (exit code {r['exit_code']})")

    # Save batch manifest
    manifest_path = REPO_ROOT / "benchmarks" / "rtl-opt" / f"batch_{args.mode}_{args.agent_model}.json"
    manifest = {
        "mode": args.mode,
        "n": args.n,
        "agent_cli": args.agent_cli,
        "agent_model": args.agent_model,
        "results": results,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\n  Manifest: {manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
