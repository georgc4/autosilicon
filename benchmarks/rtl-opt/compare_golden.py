#!/usr/bin/env python3
"""Compare RTL-OPT experiment results against golden references.

Synthesizes three versions of each design and compares cell counts:
  1. Original (suboptimal) — the starting point
  2. LLM-optimized — current rtl/ after experiments
  3. Golden reference — hand-optimized from RTL-OPT benchmark

Usage:
    python3 benchmarks/rtl-opt/compare_golden.py
    python3 benchmarks/rtl-opt/compare_golden.py --designs adder alu_64bit
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESIGNS_DIR = REPO_ROOT / "designs"
INFRA_DIR = REPO_ROOT / "infra"


def find_liberty() -> str:
    """Find Sky130 liberty file."""
    lib = os.environ.get("LIBERTY_FILE", "")
    if lib and Path(lib).exists():
        return lib
    volare = list(Path.home().glob(
        ".volare/volare/sky130/versions/*/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    ))
    if volare:
        return str(volare[0])
    sys.exit("Cannot find Sky130 liberty file. Set LIBERTY_FILE env var.")


def synth_and_count(verilog_path: Path, top_module: str, liberty: str) -> dict:
    """Synthesize a Verilog file and return cell count + area."""
    with tempfile.TemporaryDirectory() as tmp:
        json_out = Path(tmp) / "stats.json"
        netlist_out = Path(tmp) / "netlist.v"
        abc_script = INFRA_DIR / "abc_sky130.script"

        ys_script = f"""read_verilog {verilog_path}
synth -top {top_module} -flatten
dfflibmap -liberty {liberty}
abc -liberty {liberty} -D 20000 -script {abc_script}
tee -o {json_out} stat -json -liberty {liberty}
write_verilog -noattr {netlist_out}
"""
        script_path = Path(tmp) / "synth.ys"
        script_path.write_text(ys_script)

        result = subprocess.run(
            ["yosys", "-q", str(script_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()[:200]}

        try:
            data = json.loads(json_out.read_text())
            modules = data.get("modules", {})
            mod = next(iter(modules.values()), {})
            return {
                "cells": mod.get("num_cells", 0),
                "area": mod.get("area", 0.0),
            }
        except (json.JSONDecodeError, OSError) as e:
            return {"error": str(e)}


def compare_design(design_dir: Path, liberty: str) -> dict:
    """Compare original, LLM-optimized, and golden for one design."""
    name = design_dir.name.replace("rtlopt_", "")

    # Original (frozen in tb/original.v with module renamed to <name>_original)
    original_file = design_dir / "tb" / "original.v"
    # LLM-optimized (current rtl/)
    llm_file = design_dir / "rtl" / f"{name}.v"
    # Golden reference
    golden_file = design_dir / "golden" / f"{name}_ref.v"

    result = {"design": name}

    # Synthesize original
    if original_file.exists():
        orig = synth_and_count(original_file, f"{name}_original", liberty)
        result["original"] = orig
    else:
        result["original"] = {"error": "not found"}

    # Synthesize LLM version
    if llm_file.exists():
        llm = synth_and_count(llm_file, name, liberty)
        result["llm"] = llm
    else:
        result["llm"] = {"error": "not found"}

    # Synthesize golden ref
    if golden_file.exists():
        golden = synth_and_count(golden_file, f"{name}_ref", liberty)
        result["golden"] = golden
    else:
        result["golden"] = {"error": "not found"}

    return result


def main():
    parser = argparse.ArgumentParser(description="Compare RTL-OPT results against golden references")
    parser.add_argument("--designs", nargs="*", help="Subset of design names (without rtlopt_ prefix)")
    args = parser.parse_args()

    designs = sorted(DESIGNS_DIR.glob("rtlopt_*"))
    if args.designs:
        designs = [d for d in designs if d.name.replace("rtlopt_", "") in args.designs]

    if not designs:
        print("No rtlopt_* designs found. Run benchmarks/rtl-opt/setup.py first.")
        return 1

    liberty = find_liberty()
    print(f"Synthesizing {len(designs)} designs (3 variants each)...\n")

    results = []
    for design_dir in designs:
        name = design_dir.name.replace("rtlopt_", "")
        sys.stdout.write(f"  {name:25s} ")
        sys.stdout.flush()
        r = compare_design(design_dir, liberty)
        results.append(r)

        # Quick status
        orig_cells = r["original"].get("cells", "?")
        llm_cells = r["llm"].get("cells", "?")
        gold_cells = r["golden"].get("cells", "?")
        print(f"orig={orig_cells:>5}  llm={llm_cells:>5}  gold={gold_cells:>5}", end="")

        if isinstance(orig_cells, int) and isinstance(llm_cells, int) and isinstance(gold_cells, int):
            if orig_cells > 0:
                llm_pct = (1 - llm_cells / orig_cells) * 100
                gold_pct = (1 - gold_cells / orig_cells) * 100
                print(f"  | llm={llm_pct:+.1f}%  gold={gold_pct:+.1f}%", end="")

                # Did LLM match or beat golden?
                if llm_cells <= gold_cells:
                    print("  *** LLM >= GOLDEN ***", end="")
        print()

    # Summary table
    print(f"\n{'='*80}")
    print(f"{'Design':25s} {'Orig':>6s} {'LLM':>6s} {'Golden':>6s} {'LLM%':>7s} {'Gold%':>7s} {'Match':>7s}")
    print(f"{'='*80}")

    wins = ties = losses = errors = 0
    for r in results:
        name = r["design"]
        o = r["original"].get("cells", "?")
        l = r["llm"].get("cells", "?")
        g = r["golden"].get("cells", "?")

        llm_pct = gold_pct = ""
        match = ""
        if isinstance(o, int) and isinstance(l, int) and isinstance(g, int) and o > 0:
            llm_pct = f"{(1 - l / o) * 100:+.1f}%"
            gold_pct = f"{(1 - g / o) * 100:+.1f}%"
            if l < g:
                match = "WIN"
                wins += 1
            elif l == g:
                match = "TIE"
                ties += 1
            else:
                match = "LOSS"
                losses += 1
        else:
            errors += 1

        print(f"{name:25s} {str(o):>6s} {str(l):>6s} {str(g):>6s} {llm_pct:>7s} {gold_pct:>7s} {match:>7s}")

    total = wins + ties + losses
    print(f"{'='*80}")
    if total:
        print(f"LLM vs Golden:  {wins} wins, {ties} ties, {losses} losses out of {total} designs")
        print(f"Win+Tie rate: {(wins + ties) / total * 100:.0f}%")
    if errors:
        print(f"Errors: {errors} designs could not be compared")

    # Save results JSON
    out_path = REPO_ROOT / "benchmarks" / "rtl-opt" / "golden_comparison.json"
    out_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nFull results: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
