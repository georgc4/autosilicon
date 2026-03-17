#!/usr/bin/env python3
"""AutoSilicon — Design-Agnostic Yosys Synthesis Sweep Driver.

Reads a design-specific synth_config.yaml, generates parameterized Yosys
scripts from infra/yosys_template.ys, runs synthesis for each configuration,
and collects metrics into a CSV for Pareto analysis.

Adapted from the NLT accelerator synth_sweep.py — made design-agnostic by
accepting --design-dir and reading RTL file lists from config.

Usage:
    python infra/synth_sweep.py --design-dir designs/foc --preset quick
    python infra/synth_sweep.py --design-dir designs/foc --preset medium --jobs 8
    python infra/synth_sweep.py --design-dir designs/foc --single
    python infra/synth_sweep.py --design-dir designs/foc --dry-run --preset full
    python infra/synth_sweep.py --design-dir designs/foc --filter "DATA_W=32"
"""

import argparse
import csv
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("synth_sweep")

# ── Paths ─────────────────────────────────────────────────────────
INFRA_DIR = Path(__file__).resolve().parent
PROJ_ROOT = INFRA_DIR.parent
TEMPLATE_PATH = INFRA_DIR / "yosys_template.ys"


def load_config(design_dir: Path) -> dict:
    """Load the design's synth_config.yaml."""
    config_path = design_dir / "synth" / "synth_config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_param_names(cfg: dict) -> list[str]:
    """Extract ordered parameter names from config."""
    return list(cfg.get("defaults", {}).keys())


def build_grid(cfg: dict, preset: str | None, filter_str: str | None) -> list[dict]:
    """Build the list of parameter configurations to sweep."""
    param_names = get_param_names(cfg)

    if preset:
        if preset not in cfg["presets"]:
            log.error("Unknown preset '%s'. Available: %s",
                      preset, list(cfg["presets"].keys()))
            sys.exit(1)
        p = cfg["presets"][preset]
        axes = {k: p[k] for k in param_names if k in p}
    else:
        # Use defaults as single-element lists
        axes = {k: [cfg["defaults"][k]] for k in param_names}

    # Ensure all axes present
    for k in param_names:
        if k not in axes:
            axes[k] = [cfg["defaults"][k]]

    # Build Cartesian product
    values = [axes[k] for k in param_names]
    configs = [dict(zip(param_names, combo)) for combo in itertools.product(*values)]

    # Apply derive rules
    params_spec = cfg.get("parameters", {})
    for c in configs:
        for pname, pspec in params_spec.items():
            if isinstance(pspec, dict) and "derive_from" in pspec:
                # Evaluate derive expression with current config as namespace
                c[pname] = eval(pspec["derive_from"], {}, c)  # noqa: S307

    # Apply filter
    if filter_str:
        filters = {}
        for pair in filter_str.split(","):
            k, v = pair.strip().split("=")
            filters[k.strip()] = int(v.strip())
        configs = [c for c in configs if all(c.get(k) == v for k, v in filters.items())]

    # Deduplicate
    seen = set()
    unique = []
    for c in configs:
        key = tuple(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def extract_package_body(pkg_text: str) -> str:
    """Extract the body of a SystemVerilog package (between package/endpackage).

    Returns the inner declarations (localparams, typedefs, functions) that can
    be inlined into modules that import the package.
    """
    m = re.search(r"package\s+\w+\s*;(.*?)endpackage", pkg_text, flags=re.DOTALL)
    if not m:
        return ""
    body = m.group(1)
    # Strip 'real' type functions (Yosys doesn't support them)
    body = re.sub(
        r"\n\s*//[^\n]*\n\s*//[^\n]*\n\s*function\s+automatic\s+real\b.*?\bendfunction\s*\n",
        "\n",
        body,
        flags=re.DOTALL,
    )
    # Strip 'parameter int' declarations — these come from module parameters, not package
    body = re.sub(r"^\s*parameter\s+int\s+\w+\s*=.*;\s*\n", "", body, flags=re.MULTILINE)
    return body


def preprocess_rtl_for_yosys(text: str, pkg_name: str, pkg_body: str) -> str:
    """Preprocess RTL source for Yosys compatibility.

    Yosys doesn't support 'import pkg::*' at all (even inside the module body).
    Fix: strip the import and inline the package body (localparams, typedefs,
    functions) directly into the module.
    """
    import_pat = rf"^\s*import\s+{re.escape(pkg_name)}::\*;\s*\n"
    if re.search(import_pat, text, flags=re.MULTILINE):
        # Replace import with inlined package body
        text = re.sub(import_pat, f"\n    // [synth_sweep] inlined from {pkg_name}\n{pkg_body}\n", text, count=1, flags=re.MULTILINE)
    return text


def generate_yosys_script(
    cfg: dict, config_params: dict, config_id: int,
    design_dir: Path, results_dir: Path,
) -> Path:
    """Generate a per-config Yosys script from the template."""
    template = TEMPLATE_PATH.read_text()
    design_cfg = cfg["design"]
    top_module = design_cfg["top_module"]

    # Preprocess RTL for Yosys: inline package body into modules that import it.
    # Yosys doesn't support 'import pkg::*' at all.
    prep_cfg = cfg.get("prep_rtl", {})
    pkg_name = prep_cfg.get("pkg_name", f"{design_cfg['name']}_pkg")
    prep_dir = results_dir / f"config_{config_id:04d}_rtl"
    prep_dir.mkdir(exist_ok=True)

    # Extract package body from the first RTL file (assumed to be the package)
    pkg_path = design_dir / cfg["rtl_files"][0]
    pkg_body = extract_package_body(pkg_path.read_text())

    rtl_lines = []
    for rtl_file in cfg["rtl_files"]:
        rtl_path = design_dir / rtl_file
        src_text = rtl_path.read_text()
        processed = preprocess_rtl_for_yosys(src_text, pkg_name, pkg_body)
        out_path = prep_dir / rtl_path.name
        out_path.write_text(processed)
        rtl_lines.append(f"read_verilog -sv {out_path}")
    rtl_block = "\n".join(rtl_lines)

    # Build chparam lines
    param_lines = []
    for pname, pval in config_params.items():
        param_lines.append(f"chparam -set {pname:<20s} {pval:<10d} {top_module}")
    param_block = "\n".join(param_lines)

    # Output paths
    json_out = results_dir / f"config_{config_id:04d}_stats.json"
    netlist_out = results_dir / f"config_{config_id:04d}_netlist.v"

    # Resolve liberty file path
    sky130_cfg = cfg.get("sky130", {})
    liberty_file = os.environ.get("LIBERTY_FILE", "")
    if not liberty_file:
        # Default: check ~/.volare for Sky130 HD typical corner
        volare_glob = list(Path.home().glob(
            ".volare/volare/sky130/versions/*/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
        ))
        if volare_glob:
            liberty_file = str(volare_glob[0])
    if not liberty_file:
        liberty_file = sky130_cfg.get("liberty_file", "")

    # Fill template
    script = template
    script = script.replace("__DESIGN_TOP__", top_module)
    script = script.replace("__RTL_FILES__", rtl_block)
    script = script.replace("__PARAM_LIST__", param_block)
    script = script.replace("__JSON_OUT__", str(json_out))
    script = script.replace("__NETLIST_OUT__", str(netlist_out))
    script = script.replace("__LIBERTY_FILE__", liberty_file)
    script = script.replace("__ABC_SCRIPT__", str(PROJ_ROOT / "infra" / "abc_sky130.script"))

    script_path = results_dir / f"config_{config_id:04d}.ys"
    script_path.write_text(script)
    return script_path


def parse_yosys_json(json_path: Path) -> dict:
    """Parse Yosys stat -json output for cell/wire metrics."""
    try:
        with open(json_path) as f:
            data = json.load(f)
        # Yosys stat -json puts data under "modules"
        modules = data.get("modules", {})
        if isinstance(modules, dict):
            for mname, minfo in modules.items():
                return {
                    "num_cells": minfo.get("num_cells", 0),
                    "num_wires": minfo.get("num_wires", 0),
                    "num_wire_bits": minfo.get("num_wire_bits", 0),
                    "num_memories": minfo.get("num_memories", 0),
                    "num_memory_bits": minfo.get("num_memory_bits", 0),
                    "num_processes": minfo.get("num_processes", 0),
                }
    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        pass
    return {}


def run_single_config(args: tuple) -> dict:
    """Run Yosys for a single configuration. Designed for multiprocessing."""
    config_id, config_params, cfg, design_dir, results_dir, yosys_bin, timeout = args

    param_names = get_param_names(cfg)
    row = {"config_id": config_id}
    row.update(config_params)

    script_path = generate_yosys_script(cfg, config_params, config_id, design_dir, results_dir)

    log.info("Config %04d: %s", config_id,
             ", ".join(f"{k}={config_params[k]}" for k in param_names))

    t0 = time.time()
    try:
        result = subprocess.run(
            [yosys_bin, "-s", str(script_path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJ_ROOT),
        )
        elapsed = time.time() - t0
        row["synth_time_s"] = round(elapsed, 2)

        # Save stdout (contains ABC delay info) and stderr
        log_path = results_dir / f"config_{config_id:04d}_yosys.log"
        log_path.write_text(result.stdout or "")
        if result.stderr:
            err_path = results_dir / f"config_{config_id:04d}_stderr.log"
            err_path.write_text(result.stderr)

        if result.returncode != 0:
            row["status"] = "FAIL"
            log.warning("Config %04d FAILED (rc=%d)", config_id, result.returncode)
        else:
            row["status"] = "OK"
            # Parse stats
            json_path = results_dir / f"config_{config_id:04d}_stats.json"
            metrics = parse_yosys_json(json_path)
            row.update(metrics)

    except subprocess.TimeoutExpired:
        row["synth_time_s"] = timeout
        row["status"] = "TIMEOUT"
        log.warning("Config %04d TIMEOUT after %ds", config_id, timeout)

    return row


def main():
    parser = argparse.ArgumentParser(description="AutoSilicon Synthesis Sweep Driver")
    parser.add_argument("--design-dir", required=True, type=Path,
                        help="Path to design directory (e.g., designs/foc)")
    parser.add_argument("--preset", type=str, default=None,
                        help="Sweep preset name (quick, medium, full)")
    parser.add_argument("--single", action="store_true",
                        help="Run single config with defaults")
    parser.add_argument("--filter", type=str, default=None,
                        help="Filter configs (e.g., 'DATA_W=32,PIPE_DEPTH=2')")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print configs without running synthesis")
    parser.add_argument("--jobs", type=int, default=None,
                        help="Parallel jobs (default: from config)")
    args = parser.parse_args()

    design_dir = args.design_dir.resolve()
    if not design_dir.exists():
        log.error("Design directory does not exist: %s", design_dir)
        sys.exit(1)

    cfg = load_config(design_dir)
    param_names = get_param_names(cfg)
    synth_cfg = cfg.get("synthesis", {})
    yosys_bin = synth_cfg.get("yosys_binary", "yosys")
    timeout = synth_cfg.get("timeout_seconds", 120)
    jobs = args.jobs or synth_cfg.get("parallel_jobs", 4)

    # Build sweep grid
    preset = None if args.single else args.preset
    configs = build_grid(cfg, preset, args.filter)

    log.info("Design: %s (%s)", cfg["design"]["name"], cfg["design"]["top_module"])
    log.info("Sweep: %d configurations", len(configs))

    if args.dry_run:
        for i, c in enumerate(configs):
            print(f"  [{i:04d}] {', '.join(f'{k}={c[k]}' for k in param_names)}")
        return

    # Prepare results directory
    results_dir = design_dir / "synth" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Build work items
    work = [
        (i, c, cfg, design_dir, results_dir, yosys_bin, timeout)
        for i, c in enumerate(configs)
    ]

    # Run synthesis
    if jobs > 1 and len(configs) > 1:
        log.info("Running with %d parallel jobs...", jobs)
        with Pool(processes=jobs) as pool:
            results = pool.map(run_single_config, work)
    else:
        results = [run_single_config(w) for w in work]

    # Write CSV
    csv_path = results_dir / "sweep_results.csv"
    csv_columns = ["config_id"] + param_names + [
        "num_cells", "num_wires", "num_wire_bits",
        "num_memories", "num_memory_bits", "num_processes",
        "synth_time_s", "status",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    # Summary
    ok = sum(1 for r in results if r.get("status") == "OK")
    fail = sum(1 for r in results if r.get("status") == "FAIL")
    tout = sum(1 for r in results if r.get("status") == "TIMEOUT")
    log.info("Done: %d OK, %d FAIL, %d TIMEOUT", ok, fail, tout)
    log.info("Results: %s", csv_path)


if __name__ == "__main__":
    main()
