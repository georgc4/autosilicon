#!/usr/bin/env python3
"""AutoSilicon — RTL Preprocessing for Yosys/OpenLane2 Compatibility.

Creates Yosys-compatible copies of RTL files in a design's openlane src/
directory. Design-agnostic — reads config from the design directory.

Fixes applied (lessons from NLT accelerator):
  1. Strips 'import <pkg>::*' statements (Yosys can't handle these between
     module and #( parameter list)
  2. Fully qualifies bare package type/enum references with pkg:: prefix
  3. Flattens unpacked array ports to packed vectors (Yosys limitation)
  4. Removes 'real' type functions from packages (unsupported by Yosys)

Usage:
    python infra/prep_rtl.py --design-dir designs/foc
"""

import argparse
import json
import re
import sys
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parent
PROJ_ROOT = INFRA_DIR.parent


def load_design_config(design_dir: Path) -> dict:
    """Load the design's OpenLane config.json."""
    config_path = design_dir / "openlane" / get_top_name(design_dir) / "config.json"
    with open(config_path) as f:
        return json.load(f)


def get_top_name(design_dir: Path) -> str:
    """Infer top module name from openlane subdirectory."""
    openlane_dir = design_dir / "openlane"
    if openlane_dir.exists():
        subdirs = [d for d in openlane_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if subdirs:
            return subdirs[0].name
    return "unknown_top"


def load_prep_config(design_dir: Path) -> dict:
    """Load prep_rtl config from synth_config.yaml if available."""
    import yaml
    config_path = design_dir / "synth" / "synth_config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def strip_imports(text: str, pkg_name: str) -> str:
    """Strip 'import pkg::*' statements."""
    return re.sub(
        rf"^\s*import\s+{re.escape(pkg_name)}::\*;\s*\n",
        "", text, flags=re.MULTILINE,
    )


def qualify_types(text: str, pkg_name: str, pkg_types: list[str]) -> str:
    """Fully qualify bare package type references."""
    for t in pkg_types:
        text = re.sub(rf"\b(?<!::){re.escape(t)}\b", f"{pkg_name}::{t}", text)
    return text


def strip_real_functions(text: str) -> str:
    """Remove functions returning 'real' type (unsupported by Yosys)."""
    return re.sub(
        r"\n\s*//[^\n]*\n\s*//[^\n]*\n\s*function\s+automatic\s+real\b.*?\bendfunction\s*\n",
        "\n",
        text,
        flags=re.DOTALL,
    )


def flatten_unpacked_arrays(text: str) -> str:
    """Convert unpacked array ports to packed vectors for Yosys.

    Matches patterns like:
        input logic signed [WIDTH-1:0] signal [COUNT],
    Converts to:
        input logic signed [COUNT*WIDTH-1:0] signal,

    This is a generic best-effort transform. Complex cases may need
    design-specific handling.
    """
    # Match: direction type [range] name [array_dim]
    pattern = re.compile(
        r"((?:input|output|inout)\s+logic\s+(?:signed\s+)?)"
        r"\[(\w+(?:\s*-\s*1)?):0\]\s+"
        r"(\w+)\s+"
        r"\[(\w+)\]",
    )

    def replace_match(m):
        prefix = m.group(1)
        width_expr = m.group(2)
        signal = m.group(3)
        count = m.group(4)
        # Reconstruct width: if "FOO-1" -> original width is FOO
        if width_expr.replace(" ", "").endswith("-1"):
            base_width = width_expr.replace(" ", "").removesuffix("-1")
        else:
            base_width = f"({width_expr}+1)"
        return f"{prefix}[{count}*{base_width}-1:0] {signal}"

    return pattern.sub(replace_match, text)


def preprocess_file(
    src_path: Path, dst_path: Path,
    pkg_name: str | None = None,
    pkg_types: list[str] | None = None,
    is_package: bool = False,
) -> None:
    """Preprocess a single RTL file."""
    text = src_path.read_text()

    if is_package:
        text = strip_real_functions(text)

    if pkg_name and re.search(rf"import\s+{re.escape(pkg_name)}::", text):
        text = strip_imports(text, pkg_name)
        if pkg_types:
            text = qualify_types(text, pkg_name, pkg_types)

    text = flatten_unpacked_arrays(text)

    dst_path.write_text(text)


def main():
    parser = argparse.ArgumentParser(description="Preprocess RTL for Yosys compatibility")
    parser.add_argument("--design-dir", required=True, type=Path,
                        help="Path to design directory (e.g., designs/foc)")
    args = parser.parse_args()

    design_dir = args.design_dir.resolve()
    top_name = get_top_name(design_dir)
    src_dir = design_dir / "openlane" / top_name / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Load synth config for RTL file list
    synth_cfg = load_prep_config(design_dir)
    rtl_files = synth_cfg.get("rtl_files", [])
    design_info = synth_cfg.get("design", {})

    # Infer package name from first RTL file (convention: *_pkg.sv)
    pkg_name = None
    pkg_types = synth_cfg.get("prep_rtl", {}).get("pkg_types", [])
    for f in rtl_files:
        if "_pkg.sv" in f:
            pkg_name = Path(f).stem  # e.g., "foc_pkg"
            break

    print(f"Preprocessing {len(rtl_files)} RTL files into {src_dir}")
    for rtl_rel in rtl_files:
        src_path = design_dir / rtl_rel
        dst_path = src_dir / Path(rtl_rel).name
        is_pkg = "_pkg.sv" in rtl_rel
        preprocess_file(src_path, dst_path, pkg_name, pkg_types, is_pkg)
        print(f"  {rtl_rel} -> {dst_path.name}")

    # Update config.json VERILOG_FILES
    config_path = design_dir / "openlane" / top_name / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
        config["VERILOG_FILES"] = [f"dir::src/{Path(r).name}" for r in rtl_files]
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
            f.write("\n")
        print(f"Updated {config_path}")

    print("Done.")


if __name__ == "__main__":
    main()
