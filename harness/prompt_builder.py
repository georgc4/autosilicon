"""Prompt construction for AutoSilicon FE and BE modes.

Builds a self-contained prompt with RTL inlined so the agent can edit
immediately without reading files. Keeps the prompt tight and focused.
"""

import logging
import subprocess
from pathlib import Path

from . import results as res_mod

log = logging.getLogger("autosilicon.prompt")


def build_prompt(
    *,
    program_md_path: Path,
    results_tsv_path: Path,
    mode: str,
    design_dir: Path,
    pareto_frontier: list[dict],
    current_metrics: dict | None,
) -> str:
    """Assemble the full prompt string for one experiment iteration."""

    sections: list[str] = []

    # ── 1. Directive ────────────────────────────────────────────
    directive = "(Default: minimize gate count, keep tests passing.)"
    if program_md_path.is_file():
        directive = program_md_path.read_text().strip()
    sections.append(directive)

    # ── 2. Results history (last 10) ────────────────────────────
    history = res_mod.read_last_n(results_tsv_path, 10)
    if history and history.strip() != "(no results yet — header only)":
        sections.append("# Results so far\n```\n" + history + "\n```")

    # ── 3. Pareto frontier ──────────────────────────────────────
    if pareto_frontier:
        lines = "\n".join(
            "  " + "  ".join(f"{k}={v}" for k, v in pt.items())
            for pt in pareto_frontier
        )
        sections.append("# Current best (must beat or match):\n" + lines)

    # ── 4. Git log (last 10) ────────────────────────────────────
    git_log = _run_git(design_dir, ["log", "--oneline", "-10"])
    if git_log and "fatal" not in git_log:
        sections.append("# Recent changes\n```\n" + git_log + "\n```")

    # ── 5. Inline current RTL ───────────────────────────────────
    if mode == "fe":
        sections.append(_inline_rtl(design_dir))
    else:
        sections.append(_inline_be_files(design_dir))

    return "\n\n---\n\n".join(sections)


def _inline_rtl(design_dir: Path) -> str:
    """Inline all .sv files from rtl/ into the prompt."""
    rtl_dir = design_dir / "rtl"
    if not rtl_dir.is_dir():
        return "# RTL\n(no rtl/ directory found)"

    parts = ["# Current RTL (edit these files via the Edit tool)\n"]
    for sv_file in sorted(rtl_dir.glob("*.sv")):
        content = sv_file.read_text()
        rel = sv_file.relative_to(design_dir)
        parts.append(f"## {rel}\n```systemverilog\n{content}\n```")

    return "\n\n".join(parts)


def _inline_be_files(design_dir: Path) -> str:
    """Inline OpenLane config files for BE mode."""
    parts = ["# Current PnR config (edit these files)\n"]
    for pattern in ["openlane/*/config.json", "openlane/*/*.sdc", "openlane/*/pin_order.cfg"]:
        for f in sorted(design_dir.glob(pattern)):
            content = f.read_text()
            rel = f.relative_to(design_dir)
            parts.append(f"## {rel}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _run_git(cwd: Path, args: list[str]) -> str:
    """Run a git command and return stdout."""
    try:
        proc = subprocess.run(
            ["git"] + args, cwd=cwd,
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""
