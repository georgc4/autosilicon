#!/usr/bin/env python3
"""Run N independent one-shot optimization attempts for best-of-N comparison.

Each attempt starts from the same seed commit, runs exactly 1 experiment,
and records its result independently. The comparison script then picks the best.

This produces the control condition for the iterative vs. best-of-N ablation:
same compute budget (N LLM calls), but no feedback or compounding.

Usage:
    python3 scripts/run_best_of_n.py \
        --design-dir designs/picorv32 \
        --n 10 \
        --agent-cli claude --agent-model claude-opus-4-6 \
        --seed-ref HEAD

    # Dry-run (validate wiring without invoking LLM):
    python3 scripts/run_best_of_n.py \
        --design-dir designs/picorv32 --n 3 --dry-run
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run N independent one-shot optimization attempts (best-of-N ablation)")
    parser.add_argument("--design-dir", type=Path, required=True,
                        help="Design directory relative to repo root")
    parser.add_argument("--n", type=int, default=10,
                        help="Number of independent attempts (default: 10)")
    parser.add_argument("--agent-cli", choices=["claude", "codex"], default="claude")
    parser.add_argument("--agent-model", default="claude-opus-4-6")
    parser.add_argument("--seed-ref", default="HEAD",
                        help="Git ref to seed each attempt from (default: HEAD)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-per-run", type=int, default=None)
    parser.add_argument("--agent-timeout", type=int, default=None)
    parser.add_argument("--sequential", action="store_true",
                        help="Run attempts one at a time (default)")
    return parser.parse_args()


def main():
    args = parse_args()
    design_dir = args.design_dir
    model_slug = args.agent_model.replace("-", "").replace(".", "")

    results = []

    for i in range(1, args.n + 1):
        run_name = f"bon-{model_slug}-{i:03d}"
        print(f"\n{'='*60}")
        print(f"Best-of-N attempt {i}/{args.n}: {run_name}")
        print(f"{'='*60}\n")

        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "launch_agent_run.py"),
            "--design-dir", str(design_dir),
            "--agent-cli", args.agent_cli,
            "--agent-model", args.agent_model,
            "--seed-ref", args.seed_ref,
            "--run-name", run_name,
            "--max-experiments", "1",  # Single attempt per run
        ]
        if args.timeout_per_run is not None:
            cmd.extend(["--timeout-per-run", str(args.timeout_per_run)])
        if args.agent_timeout is not None:
            cmd.extend(["--agent-timeout", str(args.agent_timeout)])
        if args.dry_run:
            cmd.append("--dry-run")

        proc = subprocess.run(cmd, cwd=REPO_ROOT)
        results.append({
            "attempt": i,
            "run_name": run_name,
            "exit_code": proc.returncode,
        })

        if proc.returncode != 0:
            print(f"  WARNING: attempt {i} exited with code {proc.returncode}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Best-of-N Summary: {args.n} attempts on {design_dir}")
    print(f"{'='*60}")
    ok = sum(1 for r in results if r["exit_code"] == 0)
    print(f"  Completed: {ok}/{args.n}")

    # Save manifest for comparison script
    manifest_path = (REPO_ROOT / design_dir / "runs" / f"bon-{model_slug}-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "mode": "best-of-n",
        "n": args.n,
        "agent_cli": args.agent_cli,
        "agent_model": args.agent_model,
        "seed_ref": args.seed_ref,
        "design_dir": str(design_dir),
        "attempts": results,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  Manifest: {manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
