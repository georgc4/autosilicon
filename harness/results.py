"""results.tsv management for AutoSilicon."""

import csv
import logging
from pathlib import Path

log = logging.getLogger("autosilicon.results")

FE_COLUMNS = [
    "experiment_id", "commit", "timestamp", "status",
    "gate_count", "cell_count", "estimated_fmax_mhz", "area",
    "tests_passed", "lint_passed", "max_ulp_error", "synth_time_s",
    "description",
]

BE_COLUMNS = [
    "experiment_id", "commit", "timestamp", "status",
    "die_area_um2", "wns", "tns", "drc_violation_count", "power_mw",
    "pnr_time_s",
    "description",
]


def get_columns(mode: str) -> list[str]:
    return FE_COLUMNS if mode == "fe" else BE_COLUMNS


def init_results(path: Path, mode: str) -> None:
    """Create results.tsv with header if it doesn't exist."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = get_columns(mode)
    path.write_text("\t".join(cols) + "\n")
    log.info("Initialized %s", path)


def append_result(path: Path, mode: str, result: dict) -> None:
    """Append a result row to results.tsv."""
    cols = get_columns(mode)
    row = [str(result.get(c, "")) for c in cols]
    with open(path, "a") as f:
        f.write("\t".join(row) + "\n")


def read_last_n(path: Path, n: int = 20) -> str:
    """Read the header + last n rows of results.tsv as a string."""
    if not path.is_file():
        return "(no results yet)"
    lines = path.read_text().strip().split("\n")
    if len(lines) <= 1:
        return "(no results yet — header only)"
    header = lines[0]
    data = lines[1:]
    selected = data[-n:] if len(data) > n else data
    return header + "\n" + "\n".join(selected)


def get_last_experiment_id(path: Path) -> int:
    """Get the highest experiment_id from results.tsv, or 0 if empty."""
    if not path.is_file():
        return 0
    max_id = 0
    try:
        with open(path) as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                if _is_derived_config_row(row):
                    continue
                try:
                    eid = int(row.get("experiment_id", 0))
                    if eid > max_id:
                        max_id = eid
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return max_id


def get_latest_metrics(path: Path, mode: str) -> dict | None:
    """Get metrics from the last 'keep' row in results.tsv."""
    if not path.is_file():
        return None
    rows = []
    try:
        with open(path) as f:
            reader = csv.DictReader(f, delimiter='\t')
            rows = list(reader)
    except Exception:
        return None

    # Walk backwards to find the last canonical keep row.
    for row in reversed(rows):
        if _is_derived_config_row(row):
            continue
        if row.get("status") == "keep":
            cols = get_columns(mode)
            return {c: row.get(c, "") for c in cols}
    return None


def _is_derived_config_row(row: dict) -> bool:
    """Return True for post hoc multi-config rows that should not drive resume state."""
    config_id = (row.get("config_id") or "").strip()
    return bool(config_id and config_id not in {"0", "32"})
