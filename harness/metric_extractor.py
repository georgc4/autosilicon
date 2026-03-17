"""Parse synthesis and PnR reports for AutoSilicon.

Handles:
- Yosys stat.json for frontend metrics (gate_count, cell_count, area)
- OpenLane2 reports for backend metrics (die_area, WNS, TNS, DRC, power)
- cocotb results.xml for test classification and accuracy metrics
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("autosilicon.metrics")


# ---------------------------------------------------------------------------
# Frontend: Yosys synthesis metrics
# ---------------------------------------------------------------------------

def extract_fe_metrics(design_dir: Path) -> dict:
    """Extract frontend metrics from Yosys synthesis output.

    Looks for the stat.json file produced by `make synth-one`.
    Falls back to parsing stat.rpt if JSON is missing.
    """
    metrics = {
        "gate_count": None,
        "cell_count": None,
        "estimated_fmax_mhz": None,
        "area": None,
    }

    # Find stats JSON — could be stat.json or config_NNNN_stats.json
    stat_json = _find_file(design_dir, "*stats.json")
    if stat_json:
        metrics.update(_parse_yosys_stat_json(stat_json))
    else:
        stat_rpt = _find_file(design_dir, "stat.rpt")
        if stat_rpt:
            metrics.update(_parse_yosys_stat_rpt(stat_rpt))

    # Extract fmax from Yosys log (ABC stime -p output)
    yosys_log = _find_file(design_dir, "*yosys.log")
    if yosys_log:
        fmax = _extract_fmax_from_abc(yosys_log)
        if fmax is not None:
            metrics["estimated_fmax_mhz"] = fmax

    return metrics


def extract_fe_sweep_metrics(design_dir: Path) -> list[dict]:
    """Extract metrics from ALL configs in a sweep.

    Returns a list of dicts, each with area, estimated_fmax_mhz, and the
    parameter config that produced them.
    """
    results_dir = design_dir / "synth" / "results"
    if not results_dir.is_dir():
        return []

    points = []
    # Find all per-config stats files
    for stats_path in sorted(results_dir.glob("config_*_stats.json")):
        config_id = stats_path.name.split("_")[1]  # e.g. "0000"
        m = _parse_yosys_stat_json(stats_path)
        if not m.get("cell_count"):
            continue

        # Find matching yosys log for fmax
        yosys_log = results_dir / f"config_{config_id}_yosys.log"
        if yosys_log.is_file():
            fmax = _extract_fmax_from_abc(yosys_log)
            if fmax is not None:
                m["estimated_fmax_mhz"] = fmax

        # Read config params from the sweep CSV
        m["config_id"] = config_id
        points.append(m)

    # Enrich with parameter values from sweep_results.csv
    csv_path = results_dir / "sweep_results.csv"
    if csv_path.is_file():
        import csv
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                cid = str(int(row.get("config_id", -1))).zfill(4)
                for p in points:
                    if p.get("config_id") == cid:
                        p["config_params"] = {k: row[k] for k in row
                                               if k not in ("config_id", "status", "synth_time_s",
                                                            "num_cells", "num_wires", "num_wire_bits",
                                                            "num_memories", "num_memory_bits", "num_processes")}
                        break

    return points


def _parse_yosys_stat_json(path: Path) -> dict:
    """Parse Yosys stat -json output.

    Format: {"modules": {"\\module_name": {"num_cells": ..., "area": ..., ...}}}
    """
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to parse %s: %s", path, e)
        return {}

    modules = data.get("modules", {})
    if not modules:
        return {}

    # Take the first (usually only) module or the design-level summary
    design = data.get("design", {})
    mod = design if design else next(iter(modules.values()), {})

    cell_count = mod.get("num_cells", 0)
    area = mod.get("area", 0.0)

    # Estimate gate count: area / average gate area, or use cell count
    # For Sky130 HD, area is in lib units; gate_count ≈ cell_count for mapped netlists
    gate_count = cell_count

    result = {
        "cell_count": cell_count,
        "gate_count": gate_count,
        "area": area,
    }

    return result


def _parse_yosys_stat_rpt(path: Path) -> dict:
    """Parse text-format Yosys stat report as fallback."""
    text = path.read_text()
    result = {}

    m = re.search(r"Number of cells:\s+(\d+)", text)
    if m:
        result["cell_count"] = int(m.group(1))
        result["gate_count"] = int(m.group(1))

    m = re.search(r"Chip area.*?:\s+([\d.]+)", text)
    if m:
        result["area"] = float(m.group(1))

    return result


def _extract_fmax_from_abc(log_path: Path) -> float | None:
    """Extract estimated fmax from ABC mapping log.

    ABC prints delay in picoseconds; convert to MHz.
    Look for lines like: "Delay = 1234.56 ps"
    """
    try:
        text = log_path.read_text()
    except OSError:
        return None

    # ABC delay line (mapped delay after technology mapping)
    delays = re.findall(r"Delay\s*=\s*([\d.]+)\s*ps", text)
    if delays:
        delay_ps = float(delays[-1])  # take last (final mapping)
        if delay_ps > 0:
            return 1e6 / delay_ps  # ps -> MHz
    return None


# ---------------------------------------------------------------------------
# Backend: OpenLane2 PnR metrics
# ---------------------------------------------------------------------------

def extract_be_metrics(design_dir: Path) -> dict:
    """Extract backend metrics from OpenLane2 PnR output.

    Scans the most recent run directory under design_dir/openlane/<design>/runs/
    """
    metrics = {
        "die_area_um2": None,
        "wns": None,
        "tns": None,
        "drc_violation_count": None,
        "power_mw": None,
    }

    # Find latest run directory
    run_dir = _find_latest_run(design_dir)
    if not run_dir:
        log.warning("No OpenLane run directory found under %s", design_dir)
        return metrics

    # Die area from resolved.json
    resolved = run_dir / "resolved.json"
    if resolved.is_file():
        metrics["die_area_um2"] = _extract_die_area(resolved)

    # WNS/TNS from post-PNR STA (nom_tt corner)
    sta_dir = _find_sta_dir(run_dir, "stapostpnr")
    if sta_dir:
        tt_corner = _find_tt_corner(sta_dir)
        if tt_corner:
            wns = _parse_wns(tt_corner / "wns.max.rpt")
            tns = _parse_tns(tt_corner / "tns.max.rpt")
            if wns is not None:
                metrics["wns"] = wns
            if tns is not None:
                metrics["tns"] = tns

            power = _parse_power(tt_corner / "power.rpt")
            if power is not None:
                metrics["power_mw"] = power

    # DRC violations from magic-drc
    drc_dir = _find_stage_dir(run_dir, "magic-drc")
    if drc_dir:
        drc_count = _parse_drc(drc_dir / "reports")
        metrics["drc_violation_count"] = drc_count

    return metrics


def _find_latest_run(design_dir: Path) -> Path | None:
    """Find the most recent OpenLane run directory.

    Searches patterns:
      design_dir/openlane/*/runs/RUN_*
      design_dir/runs/RUN_*
    """
    candidates = []
    for pattern in ["openlane/*/runs/RUN_*", "runs/RUN_*"]:
        candidates.extend(design_dir.glob(pattern))
    if not candidates:
        return None
    # Sort by name (timestamp-based) and return latest
    return sorted(candidates)[-1]


def _find_sta_dir(run_dir: Path, stage_name: str) -> Path | None:
    """Find an OpenLane stage directory by partial name match."""
    return _find_stage_dir(run_dir, stage_name)


def _find_stage_dir(run_dir: Path, stage_name: str) -> Path | None:
    """Find a numbered stage directory like '54-openroad-stapostpnr'."""
    for d in sorted(run_dir.iterdir()):
        if d.is_dir() and stage_name in d.name:
            return d
    return None


def _find_tt_corner(sta_dir: Path) -> Path | None:
    """Find the nom_tt typical corner directory."""
    for d in sta_dir.iterdir():
        if d.is_dir() and "tt" in d.name and "nom" in d.name:
            return d
    # Fallback: any tt corner
    for d in sta_dir.iterdir():
        if d.is_dir() and "tt" in d.name:
            return d
    # Last resort: first directory
    for d in sta_dir.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            return d
    return None


def _extract_die_area(resolved_json: Path) -> float | None:
    """Extract die area in um^2 from resolved.json.

    DIE_AREA is [x0, y0, x1, y1] in microns.
    """
    try:
        data = json.loads(resolved_json.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    die = data.get("DIE_AREA")
    if die and len(die) == 4:
        width = die[2] - die[0]
        height = die[3] - die[1]
        return width * height
    return None


def _parse_wns(path: Path) -> float | None:
    """Parse WNS from OpenROAD report.

    Format:
      nom_tt_025C_1v80: -0.123
    or
      nom_tt_025C_1v80: 0.0
    """
    return _parse_single_metric(path)


def _parse_tns(path: Path) -> float | None:
    """Parse TNS from OpenROAD report."""
    return _parse_single_metric(path)


def _parse_single_metric(path: Path) -> float | None:
    """Parse a single float value from an OpenROAD report file.

    These reports have the format:
      <corner_name>: <value>
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text()
    except OSError:
        return None

    # Match the last line with a colon-separated value
    for line in reversed(text.strip().split("\n")):
        m = re.search(r":\s*([-\d.eE+]+)", line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _parse_power(path: Path) -> float | None:
    """Parse total power from OpenROAD power report.

    Looks for the Total line:
      Total  <internal> <switching> <leakage> <total_watts> 100.0%

    Returns power in milliwatts.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text()
    except OSError:
        return None

    for line in text.split("\n"):
        if line.startswith("Total") and "100.0%" in line:
            parts = line.split()
            # Total power (Watts) is typically the 5th numeric field
            for part in reversed(parts):
                if part.endswith("%"):
                    continue
                try:
                    watts = float(part)
                    return watts * 1000.0  # W -> mW
                except ValueError:
                    continue
    return None


def _parse_drc(reports_dir: Path) -> int:
    """Count DRC violations from magic-drc reports.

    The report file lists violations, one per line after a header.
    An empty/missing file means zero violations.
    """
    if not reports_dir.is_dir():
        return 0

    total = 0
    for rpt in reports_dir.glob("*.rpt"):
        try:
            text = rpt.read_text().strip()
        except OSError:
            continue
        if not text:
            continue
        # Count non-empty, non-header lines
        lines = [l for l in text.split("\n")
                 if l.strip() and not l.startswith("---") and not l.startswith("===")]
        # Each violation block is separated by blank lines or has specific markers
        # Simple heuristic: count lines matching violation patterns
        violations = [l for l in lines
                      if not l.startswith("Cell") and not l.startswith("Report")]
        total += len(violations)

    return total


def _find_file(base: Path, name: str) -> Path | None:
    """Find a file by name/glob pattern under base directory."""
    for p in sorted(base.rglob(name)):
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Test result parsing: cocotb results.xml
# ---------------------------------------------------------------------------

class TestResults:
    """Parsed cocotb test results with functional/accuracy distinction."""

    def __init__(self):
        self.functional_passed: bool = True  # all non-accuracy tests passed
        self.all_passed: bool = True         # every test passed (legacy)
        self.max_ulp_error: int | None = None
        self.failures: list[str] = []        # functional failure messages
        self.accuracy_info: str = ""         # accuracy metric summary

    def __repr__(self):
        return (f"TestResults(functional_passed={self.functional_passed}, "
                f"max_ulp={self.max_ulp_error}, failures={self.failures})")


# Tests whose failure does NOT constitute a functional failure — they are
# accuracy/quality metrics only.
_ACCURACY_ONLY_TESTS = {"test_accuracy_sweep"}


def parse_test_results(design_dir: Path) -> TestResults:
    """Parse cocotb results.xml files under design_dir to classify test outcomes.

    Distinguishes functional failures (hard gate) from accuracy metrics
    (quality dimension). Also extracts max ULP error from test logs.
    """
    tr = TestResults()

    # Find all results.xml files
    xml_files = list(design_dir.rglob("results.xml"))
    if not xml_files:
        log.warning("No results.xml found under %s", design_dir)
        return tr

    for xml_path in xml_files:
        _parse_one_results_xml(xml_path, tr)

    # Extract ULP metric from test logs (cocotb simulator output)
    _extract_ulp_from_logs(design_dir, tr)

    return tr


def _parse_one_results_xml(path: Path, tr: TestResults) -> None:
    """Parse a single cocotb results.xml and update TestResults."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as e:
        log.warning("Failed to parse %s: %s", path, e)
        tr.functional_passed = False
        tr.failures.append(f"XML parse error: {path}")
        return

    for tc in tree.iter("testcase"):
        name = tc.get("name", "")
        failure = tc.find("failure")

        if failure is not None:
            tr.all_passed = False
            error_msg = failure.get("error_msg", "unknown")

            if name in _ACCURACY_ONLY_TESTS:
                # Accuracy-only test: not a functional failure
                tr.accuracy_info += f"{name}: {error_msg}\n"
                log.info("Accuracy test %s: %s (not a hard gate)", name, error_msg)
            else:
                tr.functional_passed = False
                tr.failures.append(f"{name}: {error_msg}")
                log.warning("Functional test FAILED: %s: %s", name, error_msg)


def _extract_ulp_from_logs(design_dir: Path, tr: TestResults) -> None:
    """Extract CORDIC_ACCURACY max_ulp from test logs or simulator output.

    Searches for the log line emitted by test_full_sweep / test_accuracy_sweep:
      CORDIC_ACCURACY max_cos_ulp=N max_sin_ulp=N max_ulp=N
    """
    # Search results.xml sibling files and common log locations
    candidates = list(design_dir.rglob("*.log"))
    # Also check the results.xml directories for simulator output
    for xml in design_dir.rglob("results.xml"):
        candidates.extend(xml.parent.glob("*.log"))
        candidates.extend(xml.parent.glob("*.out"))
        # cocotb sometimes puts output in sim_build/
        candidates.extend((xml.parent / "sim_build").glob("*.log") if (xml.parent / "sim_build").is_dir() else [])

    max_ulp = None
    pattern = re.compile(r"CORDIC_ACCURACY.*?max_ulp=(\d+)")

    for log_path in set(candidates):
        try:
            text = log_path.read_text()
        except OSError:
            continue
        for m in pattern.finditer(text):
            ulp = int(m.group(1))
            if max_ulp is None or ulp > max_ulp:
                max_ulp = ulp

    if max_ulp is not None:
        tr.max_ulp_error = max_ulp
        log.info("Extracted CORDIC max ULP error: %d", max_ulp)
