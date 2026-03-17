#!/usr/bin/env python3
"""AutoSilicon — LLM-in-the-loop hardware design optimization.

An autonomous experiment loop inspired by Karpathy's autoresearch.
The agent iteratively modifies RTL or PnR configuration, evaluates
the result, and keeps improvements while discarding regressions.

Usage:
    python -m autosilicon.harness.autosilicon --mode fe --design-dir ./designs/my_chip
    python -m autosilicon.harness.autosilicon --mode be --design-dir ./designs/my_chip --max-experiments 50
"""

import argparse
import datetime
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Allow running as script or module
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import metric_extractor as me
from harness import pareto
from harness import prompt_builder
from harness import results as res_mod

# ── Logging ───────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("autosilicon")


# ── Configuration ─────────────────────────────────────────────────

DEFAULT_TIMEOUT_FE = 1800   # 30 minutes for lint+test+synth
DEFAULT_TIMEOUT_BE = 7200   # 2 hours for PnR (typically ~1 hour)
PROGRESS_LOG_INTERVAL = 60  # seconds between progress log lines during eval
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 10  # seconds

CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write"

# ULP accuracy ceiling — if max_ulp_error exceeds this, the agent broke
# something structural (atan table, iteration count, etc.).  Below this
# threshold ULP is just logged, not gated on.  Scales with DATA_W.
ULP_CEILING_FACTOR = 2  # ceiling = ULP_CEILING_FACTOR * DATA_W
DEFAULT_DATA_W = 16

# Files the agent must never touch, by mode
FE_FORBIDDEN_PATTERNS = [
    "Makefile", "synth/", "tb/", "testbench/", "harness/", "autosilicon/",
]
BE_FORBIDDEN_PATTERNS = [
    "Makefile", "rtl/", "synth/", "tb/", "testbench/", "harness/", "autosilicon/",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AutoSilicon: LLM-in-the-loop hardware design optimization")
    p.add_argument("--mode", choices=["fe", "be"], required=True,
                   help="fe = frontend (RTL/synth), be = backend (PnR)")
    p.add_argument("--design-dir", type=Path, required=True,
                   help="Path to the design directory")
    p.add_argument("--max-experiments", type=int, default=0,
                   help="Stop after N experiments (0 = run forever)")
    p.add_argument("--timeout-per-run", type=int, default=0,
                   help="Timeout per evaluation run in seconds "
                        "(default: 1800 for fe, 7200 for be)")
    p.add_argument("--program-md", type=Path, default=None,
                   help="Path to program.md (default: design-dir/program.md)")
    p.add_argument("--log-file", type=Path, default=None,
                   help="Log file path (default: design-dir/autosilicon.log)")
    p.add_argument("--claude-model", type=str, default=None,
                   help="Claude model to use (passed to claude CLI)")
    p.add_argument("--data-w", type=int, default=DEFAULT_DATA_W,
                   help="Design DATA_W for ULP ceiling calculation "
                        f"(ceiling = {ULP_CEILING_FACTOR} * DATA_W, default: {DEFAULT_DATA_W})")
    return p.parse_args()


def setup_logging(log_file: Path) -> None:
    """Add file handler to root logger."""
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(fh)
    log.info("Logging to %s", log_file)


# ── Git helpers ───────────────────────────────────────────────────

def git(design_dir: Path, *args: str, check: bool = True,
        timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command in the design directory."""
    cmd = ["git"] + list(args)
    log.debug("git %s", " ".join(args))
    return subprocess.run(
        cmd, cwd=design_dir, capture_output=True, text=True,
        timeout=timeout, check=check,
    )


def git_commit_all(design_dir: Path, message: str) -> str | None:
    """Stage RTL/model changes only, then commit. Returns commit hash or None."""
    git(design_dir, "add", "rtl/", "model/")
    status = git(design_dir, "status", "--porcelain")
    if not status.stdout.strip():
        log.info("No changes to commit")
        return None
    git(design_dir, "commit", "-m", message)
    result = git(design_dir, "rev-parse", "--short", "HEAD")
    return result.stdout.strip()


def git_revert_head(design_dir: Path) -> None:
    """Revert ONLY rtl/ and model/ from the last commit, preserving results/frontier."""
    try:
        # Restore rtl/ and model/ to the state before the last commit
        git(design_dir, "checkout", "HEAD~1", "--", "rtl/", "model/")
        git(design_dir, "commit", "-m", f"Revert \"{_get_head_subject(design_dir)}\"")
        log.info("Reverted RTL/model from HEAD")
    except subprocess.CalledProcessError:
        log.warning("Selective revert failed, falling back to full revert")
        try:
            git(design_dir, "revert", "HEAD", "--no-edit")
        except subprocess.CalledProcessError:
            git(design_dir, "reset", "--hard", "HEAD~1")


def _get_head_subject(design_dir: Path) -> str:
    result = git(design_dir, "log", "-1", "--format=%s", check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def git_diff_summary(design_dir: Path) -> str:
    """Get a one-line summary of the current diff."""
    result = git(design_dir, "diff", "--stat", "HEAD~1", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return "unknown changes"
    lines = result.stdout.strip().split("\n")
    return lines[-1].strip() if lines else "unknown changes"


def extract_change_description(design_dir: Path) -> str:
    """Extract a description of changes from the git diff."""
    result = git(design_dir, "diff", "HEAD~1", "--name-only", check=False)
    if result.returncode != 0:
        return "unknown"
    files = result.stdout.strip().split("\n")
    files = [f for f in files if f.strip()]
    if not files:
        return "no file changes"
    summary = git_diff_summary(design_dir)
    return f"modified {', '.join(files[:3])}{'...' if len(files) > 3 else ''} ({summary})"


# ── Scope enforcement ─────────────────────────────────────────────

def check_scope(design_dir: Path, mode: str) -> bool:
    """Verify the agent only modified allowed files. Returns True if OK."""
    result = git(design_dir, "diff", "--name-only", "HEAD~1", check=False)
    if result.returncode != 0:
        return True  # can't check, allow

    forbidden = FE_FORBIDDEN_PATTERNS if mode == "fe" else BE_FORBIDDEN_PATTERNS
    changed = [f for f in result.stdout.strip().split("\n") if f.strip()]

    for f in changed:
        for pat in forbidden:
            if f.startswith(pat) or f == pat.rstrip("/"):
                log.error("SCOPE VIOLATION: agent modified forbidden file %s", f)
                return False
    return True


# ── Claude CLI invocation ─────────────────────────────────────────

def invoke_claude(prompt: str, design_dir: Path, model: str | None = None,
                  timeout: int = 600) -> tuple[bool, str]:
    """Invoke Claude Code CLI. Returns (success, last_line_of_output)."""
    cmd = ["claude", "-p", prompt, "--allowedTools", CLAUDE_ALLOWED_TOOLS]
    if model:
        cmd.extend(["--model", model])

    log.info("Invoking Claude Code CLI (timeout=%ds)...", timeout)
    try:
        result = subprocess.run(
            cmd, cwd=design_dir, capture_output=True, text=True, timeout=timeout,
        )
        last_line = ""
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                log.info("[claude] %s", line)
                if line.strip():
                    last_line = line.strip()
        if result.returncode != 0:
            log.warning("Claude CLI exited with code %d", result.returncode)
            return False, last_line
        return True, last_line
    except subprocess.TimeoutExpired:
        log.warning("Claude CLI timed out after %ds", timeout)
        return False, ""
    except FileNotFoundError:
        log.error("Claude CLI not found — is 'claude' on PATH?")
        return False, ""


def invoke_claude_with_retry(prompt: str, design_dir: Path,
                             model: str | None = None,
                             timeout: int = 600) -> tuple[bool, str]:
    """Invoke Claude with exponential backoff retries. Returns (success, description)."""
    for attempt in range(MAX_RETRIES):
        ok, desc = invoke_claude(prompt, design_dir, model, timeout)
        if ok:
            return True, desc
        if attempt < MAX_RETRIES - 1:
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            log.info("Retrying in %ds (attempt %d/%d)...", wait, attempt + 2, MAX_RETRIES)
            time.sleep(wait)
    log.error("Claude CLI failed after %d attempts", MAX_RETRIES)
    return False, ""


# ── Evaluation ────────────────────────────────────────────────────

def run_make(design_dir: Path, target: str, timeout: int) -> tuple[bool, str, float]:
    """Run a make target as a background subprocess with progress logging.

    The subprocess runs detached — this function polls for completion and
    logs periodic progress updates so long-running jobs (e.g. PnR ~1 hour)
    are visible in the log.

    Returns (success, output, elapsed_seconds).
    """
    log_out = design_dir / f".autosilicon_make_{target}.log"
    t0 = time.monotonic()

    try:
        with open(log_out, "w") as fout:
            proc = subprocess.Popen(
                ["make", target],
                cwd=design_dir,
                stdout=fout,
                stderr=subprocess.STDOUT,
            )

        # Poll until done, logging progress periodically
        last_progress = t0
        while True:
            try:
                proc.wait(timeout=min(PROGRESS_LOG_INTERVAL, 10))
                # Process finished
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                if elapsed > timeout:
                    proc.kill()
                    proc.wait(timeout=5)
                    log.warning("make %s timed out after %.0fs — killed", target, elapsed)
                    output = _read_tail(log_out, 50)
                    return False, f"TIMEOUT after {elapsed:.0f}s\n{output}", elapsed

                now = time.monotonic()
                if now - last_progress >= PROGRESS_LOG_INTERVAL:
                    last_progress = now
                    tail = _read_tail(log_out, 1)
                    log.info("  make %s still running (%.0fs elapsed)%s",
                             target, elapsed,
                             f" — {tail}" if tail else "")

        elapsed = time.monotonic() - t0
        output = _read_tail(log_out, 100)

        if proc.returncode != 0:
            log.warning("make %s failed (rc=%d) after %.0fs", target, proc.returncode, elapsed)
            return False, output, elapsed

        log.info("make %s completed in %.0fs", target, elapsed)
        return True, output, elapsed

    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error("make %s raised %s: %s", target, type(e).__name__, e)
        return False, str(e), elapsed


def _read_tail(path: Path, n: int) -> str:
    """Read the last n lines of a file, or empty string if unavailable."""
    try:
        lines = path.read_text().strip().split("\n")
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def run_fe_evaluation(design_dir: Path, timeout: int) -> dict:
    """Run frontend evaluation pipeline: lint → test → synth.

    Test results are parsed from results.xml to distinguish functional
    failures (hard gate) from accuracy-only metrics (quality dimension).
    The make test exit code may be non-zero due to accuracy assertions,
    which are NOT treated as functional failures.
    """
    result = {
        "lint_passed": False,
        "tests_passed": False,
        "max_ulp_error": None,
        "synth_time_s": 0,
    }

    # Step 1: lint
    log.info("Running: make lint")
    ok, output, elapsed = run_make(design_dir, "lint", timeout)
    result["lint_passed"] = ok
    if not ok:
        log.warning("Lint FAILED")
        return result

    # Step 2: test (all tests must pass — functional AND accuracy)
    log.info("Running: make test")
    make_ok, output, elapsed = run_make(design_dir, "test", timeout)

    test_results = me.parse_test_results(design_dir)
    result["tests_passed"] = make_ok
    result["max_ulp_error"] = test_results.max_ulp_error

    if not make_ok:
        log.warning("Tests FAILED (rc!=0) — discarding")
        if test_results.failures:
            log.warning("Failed tests: %s", test_results.failures)
        return result

    # Step 3: sweep synthesis across all parameter configs
    log.info("Running: make sweep-medium (~54 configs)")
    ok, output, elapsed = run_make(design_dir, "sweep-medium", timeout)
    result["synth_time_s"] = elapsed
    if not ok:
        log.warning("Synthesis sweep FAILED")
        return result

    # Extract metrics from all sweep configs
    sweep_points = me.extract_fe_sweep_metrics(design_dir)
    if not sweep_points:
        log.warning("No sweep metrics extracted")
        return result

    # Use the best area point as the headline metric for results.tsv
    best = min(sweep_points, key=lambda p: p.get("area", float("inf")))
    result.update(best)
    # Attach all sweep points for Pareto frontier update
    result["_sweep_points"] = sweep_points
    log.info("Sweep: %d configs, best area=%.0f, best fmax=%.1f",
             len(sweep_points),
             min(p.get("area", 0) for p in sweep_points),
             max(p.get("estimated_fmax_mhz", 0) for p in sweep_points))
    return result


def run_be_evaluation(design_dir: Path, timeout: int) -> dict:
    """Run backend evaluation pipeline: pnr."""
    result = {
        "pnr_time_s": 0,
    }

    log.info("Running: make pnr")
    ok, output, elapsed = run_make(design_dir, "pnr", timeout)
    result["pnr_time_s"] = elapsed
    if not ok:
        log.warning("PnR FAILED")
        return result

    # Extract metrics
    metrics = me.extract_be_metrics(design_dir)
    result.update(metrics)
    return result


# ── Hard gate checks ──────────────────────────────────────────────

def fe_hard_gate(result: dict, ulp_ceiling: int) -> bool:
    """Return True if frontend hard gate FAILS (should discard)."""
    if not result.get("lint_passed"):
        log.info("HARD GATE: lint did not pass — discarding")
        return True
    if not result.get("tests_passed"):
        log.info("HARD GATE: functional tests did not pass — discarding")
        return True
    ulp = result.get("max_ulp_error")
    if ulp is not None and ulp > ulp_ceiling:
        log.info("HARD GATE: max ULP error %d exceeds ceiling %d — discarding",
                 ulp, ulp_ceiling)
        return True
    return False


def be_hard_gate(result: dict, previous: dict | None) -> bool:
    """Return True if backend hard gate FAILS (should discard)."""
    drc = result.get("drc_violation_count")
    if drc is None:
        return False  # can't check, allow
    if previous is None:
        return False  # no baseline, allow
    prev_drc = previous.get("drc_violation_count")
    if prev_drc is None:
        return False
    try:
        if int(drc) > 0 and int(drc) > int(prev_drc):
            log.info("HARD GATE: DRC violations increased (%s → %s) — discarding",
                     prev_drc, drc)
            return True
    except (ValueError, TypeError):
        pass
    return False


# ── Main loop ─────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    design_dir = args.design_dir.resolve()

    if not design_dir.is_dir():
        log.error("Design directory does not exist: %s", design_dir)
        sys.exit(1)

    # Resolve timeout default per mode
    if args.timeout_per_run == 0:
        args.timeout_per_run = DEFAULT_TIMEOUT_FE if args.mode == "fe" else DEFAULT_TIMEOUT_BE

    # Resolve paths
    program_md = args.program_md or (design_dir / "program.md")
    results_tsv = design_dir / "results.tsv"
    frontier_path = design_dir / "pareto_frontier.json"
    log_file = args.log_file or (design_dir / "autosilicon.log")

    setup_logging(log_file)
    log.info("=" * 60)
    log.info("AutoSilicon starting")
    log.info("  mode:       %s", args.mode)
    log.info("  design_dir: %s", design_dir)
    log.info("  program.md: %s", program_md)
    log.info("  timeout:    %ds per run", args.timeout_per_run)
    if args.max_experiments:
        log.info("  max_exps:   %d", args.max_experiments)
    log.info("=" * 60)

    # Verify git repo
    try:
        git(design_dir, "status")
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error("design-dir must be a git repository")
        sys.exit(1)

    # Verify claude CLI
    if not shutil.which("claude"):
        log.error("'claude' CLI not found on PATH")
        sys.exit(1)

    # Initialize results.tsv
    res_mod.init_results(results_tsv, args.mode)

    # Load Pareto frontier
    dimensions = pareto.get_dimensions(args.mode)
    frontier = pareto.load_frontier(frontier_path)

    experiment_id = 0

    while True:
        experiment_id += 1
        if args.max_experiments and experiment_id > args.max_experiments:
            log.info("Reached max experiments (%d), stopping.", args.max_experiments)
            break

        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        log.info("━" * 60)
        log.info("Experiment %d — %s", experiment_id, timestamp)
        log.info("━" * 60)

        # ── 1. Build prompt ───────────────────────────────────────
        current_metrics = res_mod.get_latest_metrics(results_tsv, args.mode)
        prompt = prompt_builder.build_prompt(
            program_md_path=program_md,
            results_tsv_path=results_tsv,
            mode=args.mode,
            design_dir=design_dir,
            pareto_frontier=frontier,
            current_metrics=current_metrics,
        )

        # ── 2. Invoke Claude ─────────────────────────────────────
        claude_ok, claude_desc = invoke_claude_with_retry(
            prompt, design_dir, model=args.claude_model, timeout=900,
        )

        if not claude_ok:
            log.warning("Claude failed — recording as crash and continuing")
            res_mod.append_result(results_tsv, args.mode, {
                "experiment_id": experiment_id,
                "commit": "",
                "timestamp": timestamp,
                "status": "crash",
                "description": "Claude CLI invocation failed",
            })
            continue

        # ── 3. Enforce scope ──────────────────────────────────────
        # Check for unauthorized changes before committing
        status = git(design_dir, "status", "--porcelain")
        if not status.stdout.strip():
            log.info("No changes made by agent — skipping evaluation")
            res_mod.append_result(results_tsv, args.mode, {
                "experiment_id": experiment_id,
                "commit": "",
                "timestamp": timestamp,
                "status": "no_changes",
                "description": "Agent made no modifications",
            })
            continue

        # ── 4. Commit before evaluation ───────────────────────────
        commit = git_commit_all(
            design_dir,
            f"autosilicon: experiment {experiment_id}",
        )

        if not commit:
            log.info("Nothing to commit after staging")
            continue

        # Check scope after commit so we can see the diff
        if not check_scope(design_dir, args.mode):
            log.warning("Scope violation detected — discarding")
            git_revert_head(design_dir)
            res_mod.append_result(results_tsv, args.mode, {
                "experiment_id": experiment_id,
                "commit": commit,
                "timestamp": timestamp,
                "status": "scope_violation",
                "description": "Agent modified forbidden files",
            })
            continue

        description = claude_desc or extract_change_description(design_dir)
        log.info("Committed %s: %s", commit, description)

        # ── 5. Run evaluation ─────────────────────────────────────
        log.info("Running evaluation...")
        if args.mode == "fe":
            eval_result = run_fe_evaluation(design_dir, args.timeout_per_run)
        else:
            eval_result = run_be_evaluation(design_dir, args.timeout_per_run)

        log.info("Evaluation result: %s", {k: v for k, v in eval_result.items()
                                            if v is not None})

        # ── 6. Hard gate check ────────────────────────────────────
        hard_gate_failed = False
        if args.mode == "fe":
            ulp_ceiling = ULP_CEILING_FACTOR * args.data_w
            hard_gate_failed = fe_hard_gate(eval_result, ulp_ceiling)
        else:
            hard_gate_failed = be_hard_gate(eval_result, current_metrics)

        # ── 7. Pareto check (all sweep points) ───────────────────
        sweep_points = eval_result.pop("_sweep_points", [])
        # Build Pareto points from sweep (or single point if no sweep)
        pareto_points = []
        for sp in (sweep_points or [eval_result]):
            pt = {dim["name"]: sp.get(dim["name"]) for dim in dimensions}
            if all(v is not None for v in pt.values()):
                pareto_points.append(pt)

        if hard_gate_failed:
            status = "discard_hard_gate"
        elif not pareto_points:
            status = "discard_no_metrics"
            log.warning("No valid metrics from any config — discarding")
        else:
            # Check if ANY sweep point is Pareto-improving
            any_improving = any(
                pareto.is_pareto_improving(pt, frontier, dimensions)
                for pt in pareto_points
            )
            if not any_improving:
                status = "discard_not_improving"
                log.info("No sweep config is Pareto-improving — discarding")
            else:
                status = "keep"

        # ── 8. Record result ──────────────────────────────────────
        row = {
            "experiment_id": experiment_id,
            "commit": commit,
            "timestamp": timestamp,
            "status": status,
            "description": description,
        }
        row.update(eval_result)
        res_mod.append_result(results_tsv, args.mode, row)

        # ── 9. Keep or discard ────────────────────────────────────
        if status == "keep":
            # Add ALL improving sweep points to frontier
            for pt in pareto_points:
                if pareto.is_pareto_improving(pt, frontier, dimensions):
                    frontier = pareto.update_frontier(pt, frontier, dimensions)
            pareto.save_frontier(frontier, frontier_path)
            log.info("✓ KEPT — experiment %d (%s)", experiment_id, description)
            log.info("  Frontier now has %d point(s) from %d sweep configs",
                     len(frontier), len(pareto_points))
        else:
            git_revert_head(design_dir)
            log.info("✗ DISCARDED — experiment %d [%s]", experiment_id, status)

    log.info("AutoSilicon finished after %d experiments.", experiment_id - 1)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, lambda *_: (log.info("\nInterrupted."), os._exit(130)))
    main()
