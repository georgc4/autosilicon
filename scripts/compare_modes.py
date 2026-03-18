#!/usr/bin/env python3
"""Compare iterative optimization results vs. best-of-N one-shot results.

Reads results.tsv files from iterative and best-of-N runs, computes
summary statistics, and outputs a comparison table.

Usage:
    python3 scripts/compare_modes.py \
        --design-dir designs/picorv32 \
        --iterative-run opus-run-1 \
        --bon-manifest bon-claudeopus46-manifest.json

    # Compare across all runs in a design:
    python3 scripts/compare_modes.py --design-dir designs/picorv32 --auto
"""

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_results_tsv(path: Path) -> list[dict]:
    """Load results.tsv and return list of experiment records."""
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            rows.append(row)
    return rows


def extract_metrics(rows: list[dict]) -> list[dict]:
    """Extract area and fmax from result rows, filtering to kept experiments."""
    metrics = []
    for row in rows:
        status = row.get("status", "")
        if status != "keep":
            continue
        try:
            area = float(row.get("area", 0))
            fmax = float(row.get("estimated_fmax_mhz", 0))
            metrics.append({"area": area, "fmax": fmax, "experiment_id": row.get("experiment_id")})
        except (ValueError, TypeError):
            continue
    return metrics


def best_ppa(metrics: list[dict]) -> dict | None:
    """Find the best PPA point (lowest area among Pareto-optimal points)."""
    if not metrics:
        return None
    # Simple: return point with lowest area
    return min(metrics, key=lambda m: m["area"])


def summarize(label: str, metrics: list[dict], total_experiments: int) -> dict:
    """Compute summary statistics for a set of results."""
    kept = len(metrics)
    if not metrics:
        return {"label": label, "total": total_experiments, "kept": 0,
                "best_area": None, "best_fmax": None, "keep_rate": 0.0}
    best = best_ppa(metrics)
    areas = [m["area"] for m in metrics]
    fmaxs = [m["fmax"] for m in metrics]
    return {
        "label": label,
        "total": total_experiments,
        "kept": kept,
        "keep_rate": kept / total_experiments if total_experiments > 0 else 0,
        "best_area": min(areas),
        "worst_area": max(areas),
        "mean_area": sum(areas) / len(areas),
        "best_fmax": max(fmaxs),
        "worst_fmax": min(fmaxs),
        "mean_fmax": sum(fmaxs) / len(fmaxs),
    }


def print_comparison(baseline: dict | None, iterative: dict, bon: dict):
    """Pretty-print comparison table."""
    print(f"\n{'Metric':<25} {'Baseline':>12} {'Iterative':>12} {'Best-of-N':>12}")
    print("-" * 65)

    if baseline:
        print(f"{'Best area (um2)':<25} {baseline.get('area', 'N/A'):>12.1f} "
              f"{iterative.get('best_area', 'N/A'):>12.1f} "
              f"{bon.get('best_area', 'N/A'):>12.1f}")
        if baseline.get('area') and iterative.get('best_area'):
            iter_pct = (1 - iterative['best_area'] / baseline['area']) * 100
            bon_pct = (1 - bon['best_area'] / baseline['area']) * 100 if bon.get('best_area') else 0
            print(f"{'  Area reduction':<25} {'':>12} {iter_pct:>11.1f}% {bon_pct:>11.1f}%")
    else:
        print(f"{'Best area (um2)':<25} {'N/A':>12} "
              f"{iterative.get('best_area', 'N/A'):>12.1f} "
              f"{bon.get('best_area', 'N/A'):>12.1f}")

    print(f"{'Best Fmax (MHz)':<25} "
          f"{'N/A' if not baseline else f'{baseline.get(\"fmax\", 0):.1f}':>12} "
          f"{iterative.get('best_fmax', 'N/A'):>12.1f} "
          f"{bon.get('best_fmax', 'N/A'):>12.1f}")

    print(f"{'Experiments':<25} {'':>12} "
          f"{iterative['total']:>12d} {bon['total']:>12d}")
    print(f"{'Kept':<25} {'':>12} "
          f"{iterative['kept']:>12d} {bon['kept']:>12d}")
    print(f"{'Keep rate':<25} {'':>12} "
          f"{iterative['keep_rate']:>11.0%} {bon['keep_rate']:>11.0%}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Compare iterative vs. best-of-N results")
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--iterative-run", type=str, default=None,
                        help="Run name for iterative results")
    parser.add_argument("--bon-manifest", type=str, default=None,
                        help="Best-of-N manifest JSON filename")
    parser.add_argument("--baseline-json", type=str, default="baseline_metrics.json",
                        help="Baseline metrics JSON file")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-discover runs in the design directory")
    args = parser.parse_args()

    design_path = REPO_ROOT / args.design_dir
    if not design_path.exists():
        print(f"Error: {design_path} not found")
        return 1

    # Load baseline
    baseline = None
    baseline_path = design_path / args.baseline_json
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())

    # Auto-discover runs
    runs_dir = design_path / "runs"
    if args.auto and runs_dir.exists():
        for entry in sorted(runs_dir.iterdir()):
            if entry.is_dir():
                results_path = entry / "results.tsv"
                if results_path.exists():
                    print(f"Found run: {entry.name}")

    # Load iterative results
    if args.iterative_run:
        iter_path = runs_dir / args.iterative_run / "results.tsv"
        iter_rows = load_results_tsv(iter_path)
        iter_metrics = extract_metrics(iter_rows)
        iter_summary = summarize("Iterative", iter_metrics, len(iter_rows))
    else:
        iter_summary = summarize("Iterative", [], 0)

    # Load best-of-N results
    if args.bon_manifest:
        manifest_path = runs_dir / args.bon_manifest
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            bon_metrics = []
            bon_total = 0
            for attempt in manifest.get("attempts", []):
                run_name = attempt["run_name"]
                results_path = runs_dir / run_name / "results.tsv"
                rows = load_results_tsv(results_path)
                bon_total += len(rows)
                bon_metrics.extend(extract_metrics(rows))
            bon_summary = summarize("Best-of-N", bon_metrics, bon_total)
        else:
            bon_summary = summarize("Best-of-N", [], 0)
    else:
        bon_summary = summarize("Best-of-N", [], 0)

    print_comparison(baseline, iter_summary, bon_summary)

    # Verdict
    if iter_summary.get("best_area") and bon_summary.get("best_area"):
        if iter_summary["best_area"] < bon_summary["best_area"]:
            diff = (1 - iter_summary["best_area"] / bon_summary["best_area"]) * 100
            print(f"VERDICT: Iterative wins by {diff:.1f}% area reduction over best-of-N")
        elif bon_summary["best_area"] < iter_summary["best_area"]:
            diff = (1 - bon_summary["best_area"] / iter_summary["best_area"]) * 100
            print(f"VERDICT: Best-of-N wins by {diff:.1f}% area reduction over iterative")
        else:
            print("VERDICT: Tie (same best area)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
