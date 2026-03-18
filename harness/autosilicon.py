#!/usr/bin/env python3
"""AutoSilicon — LLM-in-the-loop hardware design optimization.

An autonomous experiment loop inspired by Karpathy's autoresearch.
The agent iteratively modifies RTL or PnR configuration, evaluates
the result, and keeps improvements while discarding regressions.

Usage:
    python harness/autosilicon.py --mode fe --design-dir ./designs/my_chip
    python harness/autosilicon.py --mode be --design-dir ./designs/my_chip --max-experiments 50
"""

import argparse
import datetime
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

# Allow running as script or module
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import metric_extractor as me
from harness import pareto
from harness import prompt_builder
from harness import results as res_mod
from harness import run_status as status_mod

# ── Logging ───────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("autosilicon")


# ── Configuration ─────────────────────────────────────────────────

DEFAULT_TIMEOUT_FE = 1800   # 30 minutes for lint+test+synth
DEFAULT_TIMEOUT_BE = 7200   # 2 hours for PnR (typically ~1 hour)
DEFAULT_AGENT_TIMEOUT = 900
PROGRESS_LOG_INTERVAL = 60  # seconds between progress log lines during eval
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 10  # seconds

CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write"
SUPPORTED_AGENT_CLIS = ("claude", "codex")

# Track the currently-running agent subprocess so Ctrl-C can kill it.
# Must be module-level so invoke_claude's `global _active_agent_proc`
# and _die() reference the same variable.
_active_agent_proc = None

def _die(signum, _frame):
    proc = _active_agent_proc
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signum)
        except OSError:
            pass
    subprocess.run(["stty", "sane"], check=False)
    os._exit(130)
AGENT_EXECUTABLE_CANDIDATES = {
    "codex": (
        "~/Applications/Codex.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ),
    "claude": (),
}
IGNORABLE_UNTRACKED_BASENAMES = {
    ".venv",
    "{W}",
    "results.xml",
}
IGNORABLE_UNTRACKED_PARTS = {
    "__pycache__",
    "obj_dir",
    "sim_build",
    ".pytest_cache",
    "test-results",
}
IGNORABLE_UNTRACKED_PREFIXES = (
    ".autosilicon_make_",
)
IGNORABLE_UNTRACKED_SUFFIXES = (
    ".vcd",
    ".fst",
    ".log",
)

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
                   help="Run at most N new experiments in this invocation (0 = run forever)")
    p.add_argument("--timeout-per-run", type=int, default=0,
                   help="Timeout per evaluation run in seconds "
                        "(default: 1800 for fe, 7200 for be)")
    p.add_argument("--agent-timeout", type=int, default=DEFAULT_AGENT_TIMEOUT,
                   help=f"Timeout per agent invocation in seconds (default: {DEFAULT_AGENT_TIMEOUT})")
    p.add_argument("--program-md", type=Path, default=None,
                   help="Path to program.md (default: design-dir/program.md)")
    p.add_argument("--artifacts-dir", type=Path, default=None,
                   help="Directory for run artifacts (default: design-dir)")
    p.add_argument("--log-file", type=Path, default=None,
                   help="Log file path (default: design-dir/autosilicon.log)")
    p.add_argument("--status-file", type=Path, default=None,
                   help="Live status JSON path (default: design-dir/autosilicon_status.json)")
    p.add_argument("--agent-cli", choices=SUPPORTED_AGENT_CLIS, default="claude",
                   help="Agent CLI to use for autonomous edits (default: claude)")
    p.add_argument("--agent-model", type=str, default=None,
                   help="Model override passed to the selected agent CLI")
    p.add_argument("--claude-model", type=str, default=None,
                   help="Deprecated alias for --agent-model when --agent-cli=claude")
    p.add_argument("--dry-run", action="store_true",
                   help="Build prompt and validate run wiring without invoking the agent")
    p.add_argument("--data-w", type=int, default=DEFAULT_DATA_W,
                   help="Design DATA_W for ULP ceiling calculation "
                        f"(ceiling = {ULP_CEILING_FACTOR} * DATA_W, default: {DEFAULT_DATA_W})")
    return p.parse_args()


def setup_logging(log_file: Path) -> None:
    """Add file handler to root logger."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(fh)
    log.info("Logging to %s", log_file)


def resolve_agent_model(args: argparse.Namespace) -> str | None:
    """Resolve the model override, keeping --claude-model as a compatibility alias."""
    if args.agent_model:
        return args.agent_model
    if args.claude_model and args.agent_cli == "claude":
        return args.claude_model
    return None


def resolve_agent_executable(agent_cli: str) -> str | None:
    """Locate the requested agent CLI on PATH or in known app bundle locations."""
    found = shutil.which(agent_cli)
    if found:
        return found

    for candidate in AGENT_EXECUTABLE_CANDIDATES.get(agent_cli, ()):
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    return None


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


def _status_path_from_line(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path


def is_ignorable_untracked_status(line: str) -> bool:
    """Return True for generated untracked files that should not block runs."""
    if not line.startswith("?? "):
        return False

    raw_path = _status_path_from_line(line)
    parts = PurePosixPath(raw_path).parts
    basename = parts[-1] if parts else raw_path

    if basename in IGNORABLE_UNTRACKED_BASENAMES:
        return True
    if any(part in IGNORABLE_UNTRACKED_PARTS for part in parts):
        return True
    if basename.startswith(IGNORABLE_UNTRACKED_PREFIXES):
        return True
    if basename.endswith(IGNORABLE_UNTRACKED_SUFFIXES):
        return True
    return False


def git_status_lines(design_dir: Path) -> list[str]:
    """Return meaningful status lines, filtering generated untracked artifacts."""
    result = git(design_dir, "status", "--porcelain", check=False)
    if result.returncode != 0:
        return []
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return [line for line in lines if not is_ignorable_untracked_status(line)]


def git_commit_all(design_dir: Path, message: str) -> str | None:
    """Stage RTL/model changes only, then commit. Returns commit hash or None."""
    paths = ["rtl/"] + (["model/"] if (design_dir / "model").is_dir() else [])
    git(design_dir, "add", *paths)
    if not git_status_lines(design_dir):
        log.info("No changes to commit")
        return None
    git(design_dir, "commit", "-m", message)
    result = git(design_dir, "rev-parse", "--short", "HEAD")
    return result.stdout.strip()


def git_revert_head(design_dir: Path) -> None:
    """Revert ONLY rtl/ and model/ from the last commit, preserving results/frontier."""
    try:
        # Restore rtl/ and model/ to the state before the last commit
        paths = ["rtl/"] + (["model/"] if (design_dir / "model").is_dir() else [])
        git(design_dir, "checkout", "HEAD~1", "--", *paths)
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


# ── Agent CLI invocation ──────────────────────────────────────────

def invoke_claude(prompt: str, design_dir: Path, executable: str,
                  model: str | None = None,
                  timeout: int = 600) -> tuple[bool, str]:
    """Invoke Claude Code CLI. Returns (success, last_line_of_output)."""
    cmd = [executable, "-p", prompt, "--allowedTools", CLAUDE_ALLOWED_TOOLS]
    if model:
        cmd.extend(["--model", model])

    log.info("Invoking Claude Code CLI (timeout=%ds)...", timeout)
    # Save terminal state before claude mangles it
    try:
        saved_tty = subprocess.run(["stty", "-g"], capture_output=True, text=True).stdout.strip()
    except Exception:
        saved_tty = None

    try:
        proc = subprocess.Popen(
            cmd, cwd=design_dir, start_new_session=True,
        )
        # Expose to signal handler so Ctrl-C can kill it
        global _active_agent_proc  # noqa: PLW0603
        _active_agent_proc = proc
        proc.wait(timeout=timeout)
        _active_agent_proc = None
        if proc.returncode != 0:
            log.warning("Claude CLI exited with code %d", proc.returncode)
            return False, ""
        return True, ""
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        _active_agent_proc = None
        log.warning("Claude CLI timed out after %ds", timeout)
        return False, ""
    except FileNotFoundError:
        log.error("Claude CLI not found — is 'claude' on PATH?")
        return False, ""
    finally:
        # Always restore terminal state
        if saved_tty:
            subprocess.run(["stty", saved_tty], check=False)
        else:
            subprocess.run(["stty", "sane"], check=False)


def invoke_codex(prompt: str, design_dir: Path, executable: str,
                 model: str | None = None,
                 timeout: int = 600) -> tuple[bool, str]:
    """Invoke Codex CLI in non-interactive exec mode."""
    cmd = [executable, "exec", "--full-auto", "-C", str(design_dir)]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    log.info("Invoking Codex CLI (timeout=%ds)...", timeout)
    try:
        proc = subprocess.Popen(
            cmd, cwd=design_dir, start_new_session=True,
        )
        global _active_agent_proc  # noqa: PLW0603
        _active_agent_proc = proc
        proc.wait(timeout=timeout)
        _active_agent_proc = None
        if proc.returncode != 0:
            log.warning("Codex CLI exited with code %d", proc.returncode)
            return False, ""
        return True, ""
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        _active_agent_proc = None
        log.warning("Codex CLI timed out after %ds", timeout)
        return False, ""
    except FileNotFoundError:
        log.error("Codex CLI not found — is 'codex' on PATH?")
        return False, ""


def invoke_agent(agent_cli: str, prompt: str, design_dir: Path,
                 executable: str,
                 model: str | None = None,
                 timeout: int = 600) -> tuple[bool, str]:
    """Invoke the selected agent CLI."""
    if agent_cli == "claude":
        return invoke_claude(prompt, design_dir, executable, model, timeout)
    if agent_cli == "codex":
        return invoke_codex(prompt, design_dir, executable, model, timeout)
    raise ValueError(f"unsupported agent CLI: {agent_cli}")


def invoke_agent_with_retry(agent_cli: str, prompt: str, design_dir: Path,
                            executable: str,
                            model: str | None = None,
                            timeout: int = 600) -> tuple[bool, str]:
    """Invoke the selected agent with exponential backoff retries."""
    for attempt in range(MAX_RETRIES):
        ok, desc = invoke_agent(agent_cli, prompt, design_dir, executable, model, timeout)
        if ok:
            return True, desc
        if attempt < MAX_RETRIES - 1:
            wait = RETRY_BACKOFF_BASE * (2 ** attempt)
            log.info("Retrying in %ds (attempt %d/%d)...", wait, attempt + 2, MAX_RETRIES)
            time.sleep(wait)
    log.error("%s CLI failed after %d attempts", agent_cli.capitalize(), MAX_RETRIES)
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


def run_fe_evaluation(design_dir: Path, timeout: int, phase_hook=None) -> dict:
    """Run frontend evaluation pipeline: lint → test → single-config synth."""
    result = {
        "lint_passed": False,
        "tests_passed": False,
        "max_ulp_error": None,
        "synth_time_s": 0,
    }

    # Step 1: lint
    if phase_hook:
        phase_hook("evaluating_lint")
    log.info("Running: make lint")
    ok, output, elapsed = run_make(design_dir, "lint", timeout)
    result["lint_passed"] = ok
    if not ok:
        log.warning("Lint FAILED")
        return result

    # Step 2: test
    if phase_hook:
        phase_hook("evaluating_test")
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

    # Step 3: synthesize the canonical default configuration only
    if phase_hook:
        phase_hook("evaluating_synth")
    log.info("Running: make synth-one")
    ok, output, elapsed = run_make(design_dir, "synth-one", timeout)
    result["synth_time_s"] = elapsed
    if not ok:
        log.warning("Synthesis FAILED")
        return result

    metrics = me.extract_fe_metrics(design_dir)
    if not metrics.get("area") or not metrics.get("estimated_fmax_mhz"):
        log.warning("No default-config synthesis metrics extracted")
        return result

    result.update(metrics)
    return result


def run_be_evaluation(design_dir: Path, timeout: int, phase_hook=None) -> dict:
    """Run backend evaluation pipeline: pnr."""
    result = {
        "pnr_time_s": 0,
    }

    if phase_hook:
        phase_hook("evaluating_pnr")
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
    agent_model = resolve_agent_model(args)
    agent_executable = resolve_agent_executable(args.agent_cli)

    if not design_dir.is_dir():
        log.error("Design directory does not exist: %s", design_dir)
        sys.exit(1)

    # Resolve timeout default per mode
    if args.timeout_per_run == 0:
        args.timeout_per_run = DEFAULT_TIMEOUT_FE if args.mode == "fe" else DEFAULT_TIMEOUT_BE

    # Resolve paths
    program_md = args.program_md or (design_dir / "program.md")
    if args.artifacts_dir:
        artifacts_dir = (
            args.artifacts_dir.resolve()
            if args.artifacts_dir.is_absolute()
            else (design_dir / args.artifacts_dir).resolve()
        )
    else:
        artifacts_dir = design_dir

    results_tsv = artifacts_dir / "results.tsv"
    frontier_path = artifacts_dir / "pareto_frontier.json"
    log_file = args.log_file or (artifacts_dir / "autosilicon.log")
    status_file = args.status_file or (artifacts_dir / "autosilicon_status.json")
    prompt_file = artifacts_dir / ".autosilicon_prompt.txt"

    setup_logging(log_file)
    log.info("=" * 60)
    log.info("AutoSilicon starting")
    log.info("  mode:       %s", args.mode)
    log.info("  design_dir: %s", design_dir)
    log.info("  artifacts:  %s", artifacts_dir)
    log.info("  agent_cli:  %s", args.agent_cli)
    if agent_executable:
        log.info("  agent_bin:  %s", agent_executable)
    if agent_model:
        log.info("  agent_model:%s", agent_model)
    else:
        log.warning("  agent_model: (CLI default; set --agent-model for publishable provenance)")
    log.info("  program.md: %s", program_md)
    log.info("  timeout:    %ds per run", args.timeout_per_run)
    log.info("  agent_tmo:  %ds per run", args.agent_timeout)
    log.info("  status:     %s", status_file)
    if args.dry_run:
        log.info("  dry_run:    enabled")
    if args.max_experiments:
        log.info("  max_exps:   %d", args.max_experiments)
    log.info("=" * 60)

    # Verify git repo
    try:
        git(design_dir, "status")
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error("design-dir must be a git repository")
        sys.exit(1)

    # Verify selected agent CLI
    if not agent_executable:
        log.error("'%s' CLI not found on PATH or known app bundle paths", args.agent_cli)
        sys.exit(1)

    # Initialize results.tsv
    res_mod.init_results(results_tsv, args.mode)

    # Load Pareto frontier
    dimensions = pareto.get_dimensions(args.mode)
    frontier = pareto.load_frontier(frontier_path)

    run_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"

    def update_live_status(**fields: object) -> dict:
        payload = {
            "head_commit": status_mod.git_head(design_dir),
            "git_dirty": status_mod.git_is_dirty(design_dir),
            "frontier_size": len(frontier),
        }
        payload.update(fields)
        return status_mod.update(status_file, **payload)

    update_live_status(
        run_id=run_id,
        state="starting",
        phase="startup",
        mode=args.mode,
        design_dir=str(design_dir),
        artifacts_dir=str(artifacts_dir),
        agent_cli=args.agent_cli,
        agent_executable=agent_executable,
        agent_model=agent_model or "cli-default",
        program_md=str(program_md),
        log_file=str(log_file),
        status_file=str(status_file),
        results_tsv=str(results_tsv),
        frontier_path=str(frontier_path),
        max_experiments=args.max_experiments,
        timeout_per_run=args.timeout_per_run,
        agent_timeout=args.agent_timeout,
        started_at=status_mod.now_iso(),
        dry_run=args.dry_run,
    )

    # Resume from last experiment ID in results.tsv
    experiment_id = res_mod.get_last_experiment_id(results_tsv)
    starting_experiment_id = experiment_id
    last_completed_experiment = experiment_id
    update_live_status(
        state="idle",
        phase="idle",
        last_completed_experiment=last_completed_experiment,
        starting_experiment_id=starting_experiment_id,
    )

    while True:
        if args.max_experiments and (experiment_id - starting_experiment_id) >= args.max_experiments:
            log.info("Reached max experiments (%d), stopping.", args.max_experiments)
            break
        experiment_id += 1

        # Check for stop file (touch designs/foc/.stop to halt)
        stop_file = design_dir / ".stop"
        if stop_file.exists():
            stop_file.unlink()
            log.info("Stop file detected — exiting gracefully.")
            break

        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        log.info("━" * 60)
        log.info("Experiment %d — %s", experiment_id, timestamp)
        log.info("━" * 60)
        update_live_status(
            state="running",
            phase="building_prompt",
            current_experiment=experiment_id,
            last_completed_experiment=last_completed_experiment,
        )

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
        prompt_file.write_text(prompt)

        if args.dry_run:
            log.info("Dry run complete — prompt written to %s", prompt_file)
            update_live_status(
                state="dry_run_complete",
                phase="dry_run_complete",
                current_experiment=experiment_id,
                prompt_file=str(prompt_file),
            )
            break

        # ── 2. Invoke agent ──────────────────────────────────────
        update_live_status(
            state="running",
            phase="invoking_agent",
            current_experiment=experiment_id,
            prompt_file=str(prompt_file),
        )
        agent_ok, agent_desc = invoke_agent_with_retry(
            args.agent_cli, prompt, design_dir, agent_executable,
            model=agent_model, timeout=args.agent_timeout,
        )

        if not agent_ok:
            log.warning("%s failed — recording as crash and continuing", args.agent_cli.capitalize())
            res_mod.append_result(results_tsv, args.mode, {
                "experiment_id": experiment_id,
                "commit": "",
                "timestamp": timestamp,
                "status": "crash",
                "description": f"{args.agent_cli} CLI invocation failed",
            })
            last_completed_experiment = experiment_id
            update_live_status(
                state="running",
                phase="agent_failed",
                current_experiment=experiment_id,
                last_completed_experiment=last_completed_experiment,
                last_status="crash",
                last_commit="",
                last_description=f"{args.agent_cli} CLI invocation failed",
            )
            continue

        # ── 3. Enforce scope ──────────────────────────────────────
        # Check for unauthorized changes before committing
        if not git_status_lines(design_dir):
            log.info("No changes made by agent — skipping evaluation")
            res_mod.append_result(results_tsv, args.mode, {
                "experiment_id": experiment_id,
                "commit": "",
                "timestamp": timestamp,
                "status": "no_changes",
                "description": "Agent made no modifications",
            })
            last_completed_experiment = experiment_id
            update_live_status(
                state="running",
                phase="idle",
                current_experiment=experiment_id,
                last_completed_experiment=last_completed_experiment,
                last_status="no_changes",
                last_commit="",
                last_description="Agent made no modifications",
            )
            continue

        # ── 4. Commit before evaluation ───────────────────────────
        update_live_status(
            state="running",
            phase="committing",
            current_experiment=experiment_id,
        )
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
            last_completed_experiment = experiment_id
            update_live_status(
                state="running",
                phase="reverted",
                current_experiment=experiment_id,
                last_completed_experiment=last_completed_experiment,
                last_status="scope_violation",
                last_commit=commit,
                last_description="Agent modified forbidden files",
            )
            continue

        # Read the agent summary from file, fall back to git diff description
        summary_file = design_dir / "rtl" / ".change_summary"
        if summary_file.is_file():
            description = summary_file.read_text().strip() or extract_change_description(design_dir)
            summary_file.unlink(missing_ok=True)
        else:
            description = agent_desc or extract_change_description(design_dir)
        log.info("Committed %s: %s", commit, description)

        # ── 5. Run evaluation ─────────────────────────────────────
        log.info("Running evaluation...")
        update_live_status(
            state="running",
            phase="evaluating",
            current_experiment=experiment_id,
            last_commit=commit,
            last_description=description,
        )
        if args.mode == "fe":
            eval_result = run_fe_evaluation(
                design_dir,
                args.timeout_per_run,
                phase_hook=lambda phase: update_live_status(
                    state="running",
                    phase=phase,
                    current_experiment=experiment_id,
                    last_commit=commit,
                    last_description=description,
                ),
            )
        else:
            eval_result = run_be_evaluation(
                design_dir,
                args.timeout_per_run,
                phase_hook=lambda phase: update_live_status(
                    state="running",
                    phase=phase,
                    current_experiment=experiment_id,
                    last_commit=commit,
                    last_description=description,
                ),
            )

        log.info("Evaluation result: %s", {k: v for k, v in eval_result.items()
                                            if v is not None})

        # ── 6. Hard gate check ────────────────────────────────────
        hard_gate_failed = False
        if args.mode == "fe":
            ulp_ceiling = ULP_CEILING_FACTOR * args.data_w
            hard_gate_failed = fe_hard_gate(eval_result, ulp_ceiling)
        else:
            hard_gate_failed = be_hard_gate(eval_result, current_metrics)

        # ── 7. Pareto check (single evaluated configuration) ─────
        pareto_points = []
        pt = {dim["name"]: eval_result.get(dim["name"]) for dim in dimensions}
        if all(v is not None for v in pt.values()):
            pareto_points.append(pt)

        if hard_gate_failed:
            status = "discard_hard_gate"
        elif not pareto_points:
            status = "discard_no_metrics"
            log.warning("No valid metrics from any config — discarding")
        else:
            if not pareto.is_pareto_improving(pareto_points[0], frontier, dimensions):
                status = "discard_not_improving"
                log.info("Default config is not Pareto-improving — discarding")
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
        last_completed_experiment = experiment_id

        # ── 9. Keep or discard ────────────────────────────────────
        if status == "keep":
            frontier = pareto.update_frontier(pareto_points[0], frontier, dimensions)
            pareto.save_frontier(frontier, frontier_path)
            log.info("✓ KEPT — experiment %d (%s)", experiment_id, description)
            log.info("  Frontier now has %d point(s)", len(frontier))
            update_live_status(
                state="running",
                phase="kept",
                current_experiment=experiment_id,
                last_completed_experiment=last_completed_experiment,
                last_status=status,
                last_commit=commit,
                last_description=description,
                last_metrics={k: v for k, v in eval_result.items() if v is not None},
            )
        else:
            git_revert_head(design_dir)
            log.info("✗ DISCARDED — experiment %d [%s]", experiment_id, status)
            update_live_status(
                state="running",
                phase="reverted",
                current_experiment=experiment_id,
                last_completed_experiment=last_completed_experiment,
                last_status=status,
                last_commit=commit,
                last_description=description,
                last_metrics={k: v for k, v in eval_result.items() if v is not None},
            )

    log.info("AutoSilicon finished after %d experiment(s) this run.", experiment_id - starting_experiment_id)
    update_live_status(
        state="dry_run_complete" if args.dry_run else "completed",
        phase="dry_run_complete" if args.dry_run else "completed",
        current_experiment=None,
        last_completed_experiment=last_completed_experiment,
    )


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, _die)
    signal.signal(signal.SIGQUIT, _die)
    main()
