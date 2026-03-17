"""Pareto frontier tracking and dominance checking for AutoSilicon."""

import json
import logging
from pathlib import Path

log = logging.getLogger("autosilicon.pareto")


def dominates(a: dict, b: dict, dimensions: list[dict]) -> bool:
    """Return True if point `a` dominates point `b`.

    A point dominates another if it is better-or-equal on ALL dimensions
    and strictly better on at least one.

    Each dimension is a dict: {"name": str, "direction": "minimize"|"maximize"}.
    """
    dominated_any = False
    for dim in dimensions:
        name = dim["name"]
        va, vb = a.get(name), b.get(name)
        if va is None or vb is None:
            return False
        if dim["direction"] == "minimize":
            if va > vb:
                return False
            if va < vb:
                dominated_any = True
        else:  # maximize
            if va < vb:
                return False
            if va > vb:
                dominated_any = True
    return dominated_any


def is_pareto_improving(new_point: dict, frontier: list[dict],
                        dimensions: list[dict]) -> bool:
    """Return True if new_point is not dominated by any existing frontier point.

    A new result is Pareto-improving if it either dominates something on
    the frontier or lives in a new tradeoff region (i.e., no frontier
    point dominates it).
    """
    for fp in frontier:
        if dominates(fp, new_point, dimensions):
            return False
    return True


def update_frontier(new_point: dict, frontier: list[dict],
                    dimensions: list[dict]) -> list[dict]:
    """Add new_point to frontier, removing any points it dominates."""
    updated = [fp for fp in frontier if not dominates(new_point, fp, dimensions)]
    updated.append(new_point)
    return updated


def load_frontier(path: Path) -> list[dict]:
    """Load Pareto frontier from JSON file."""
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to load frontier from %s, starting fresh", path)
        return []


def save_frontier(frontier: list[dict], path: Path) -> None:
    """Save Pareto frontier to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frontier, indent=2) + "\n")


# Dimension definitions for each mode
FE_DIMENSIONS = [
    {"name": "gate_count", "direction": "minimize"},
]

BE_DIMENSIONS = [
    {"name": "die_area_um2", "direction": "minimize"},
    {"name": "wns", "direction": "maximize"},  # want positive (met timing)
    {"name": "power_mw", "direction": "minimize"},
]


def get_dimensions(mode: str) -> list[dict]:
    """Return Pareto dimensions for the given mode."""
    return FE_DIMENSIONS if mode == "fe" else BE_DIMENSIONS
