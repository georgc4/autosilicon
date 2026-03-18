#!/usr/bin/env python3
"""Launch or resume an isolated AutoSilicon run from a seed reference.

This keeps the mutable experiment workspace in a hidden git worktree while
writing logs/results back into the primary repo's design run directory so the
main dashboard can visualize the run.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKTREE_ROOT = Path(".autosilicon-worktrees")
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
WORKTREE_LOCAL_EXCLUDES = (
    ".venv",
    "{W}",
    "test-results/",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or resume an isolated AutoSilicon run from a seed ref.",
    )
    parser.add_argument(
        "--design-dir",
        type=Path,
        default=Path("designs/foc"),
        help="Design directory relative to the repo root (default: designs/foc)",
    )
    parser.add_argument(
        "--mode",
        choices=["fe", "be"],
        default="fe",
        help="AutoSilicon mode to run (default: fe)",
    )
    parser.add_argument(
        "--agent-cli",
        choices=["claude", "codex"],
        default="codex",
        help="Agent CLI to invoke (default: codex)",
    )
    parser.add_argument(
        "--agent-model",
        default="gpt-5.4",
        help="Explicit model name for the agent CLI (default: gpt-5.4)",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Stable run identifier (default: derived from the model name)",
    )
    parser.add_argument(
        "--seed-ref",
        default=None,
        help="Git ref used to seed rtl/ and model/ (default: baseline_metrics.json commit)",
    )
    parser.add_argument(
        "--max-experiments",
        type=int,
        default=1,
        help="How many new experiments to run in this invocation (default: 1)",
    )
    parser.add_argument(
        "--timeout-per-run",
        type=int,
        default=None,
        help="Override harness evaluation timeout in seconds",
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=None,
        help="Override per-agent timeout in seconds",
    )
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=DEFAULT_WORKTREE_ROOT,
        help="Hidden directory under the repo root for launcher-managed worktrees",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare the run and invoke the harness in dry-run mode",
    )
    return parser.parse_args()


def run(cmd: list[str], cwd: Path = REPO_ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, text=True, check=check)


def capture(cmd: list[str], cwd: Path = REPO_ROOT, check: bool = True) -> str:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)
    return result.stdout.strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "", value.lower())
    return slug or "run"


def resolve_design_dir(path: Path) -> tuple[Path, Path]:
    if path.is_absolute():
        design_dir = path.resolve()
        design_rel = design_dir.relative_to(REPO_ROOT)
    else:
        design_rel = path
        design_dir = (REPO_ROOT / path).resolve()
    if not design_dir.is_dir():
        raise SystemExit(f"design directory does not exist: {design_dir}")
    return design_dir, design_rel


def discover_seed_ref(design_dir: Path) -> str:
    baseline_path = design_dir / "baseline_metrics.json"
    if not baseline_path.is_file():
        raise SystemExit(
            f"no baseline_metrics.json found at {baseline_path}; pass --seed-ref explicitly"
        )
    payload = json.loads(baseline_path.read_text())
    seed_ref = payload.get("commit")
    if not seed_ref:
        raise SystemExit(f"{baseline_path} does not contain a baseline commit")
    return str(seed_ref)


def branch_exists(branch_name: str) -> bool:
    proc = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=REPO_ROOT,
        text=True,
    )
    return proc.returncode == 0


def _status_path_from_line(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip()
    return path


def is_ignorable_untracked_status(line: str) -> bool:
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


def git_status_is_clean(worktree_path: Path) -> bool:
    status = capture(["git", "status", "--porcelain"], cwd=worktree_path, check=False)
    if not status:
        return True
    lines = [line for line in status.splitlines() if line.strip()]
    lines = [line for line in lines if not is_ignorable_untracked_status(line)]
    return not lines


def ensure_worktree_excludes(worktree_path: Path) -> None:
    exclude_path = Path(capture(["git", "rev-parse", "--git-path", "info/exclude"], cwd=worktree_path))
    if not exclude_path.is_absolute():
        exclude_path = (worktree_path / exclude_path).resolve()
    exclude_path.parent.mkdir(parents=True, exist_ok=True)

    existing = exclude_path.read_text().splitlines() if exclude_path.exists() else []
    new_lines = list(existing)
    changed = False
    for pattern in WORKTREE_LOCAL_EXCLUDES:
        if pattern not in existing:
            new_lines.append(pattern)
            changed = True

    if changed:
        exclude_path.write_text("\n".join(new_lines).rstrip() + "\n")


def ensure_shared_venv(worktree_path: Path) -> None:
    source_venv = REPO_ROOT / ".venv"
    target_venv = worktree_path / ".venv"
    if not source_venv.exists() or target_venv.exists():
        return
    target_venv.symlink_to(source_venv)


def prepare_worktree(
    worktree_path: Path,
    branch_name: str,
    seed_ref: str,
    design_rel: Path,
    run_name: str,
) -> bool:
    created = False
    if not worktree_path.exists():
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if branch_exists(branch_name):
            run(["git", "worktree", "add", str(worktree_path), branch_name])
        else:
            run(["git", "worktree", "add", "-b", branch_name, str(worktree_path), "HEAD"])
        created = True

    ensure_shared_venv(worktree_path)
    ensure_worktree_excludes(worktree_path)

    if created:
        # Only checkout paths that exist in the seed ref
        seed_paths = []
        for subdir in ("rtl", "model"):
            path = str(design_rel / subdir)
            probe = capture(
                ["git", "ls-tree", "--name-only", seed_ref, path + "/"],
                cwd=worktree_path,
                check=False,
            )
            if probe:
                seed_paths.append(path)

        if seed_paths:
            run(
                ["git", "checkout", seed_ref, "--"] + seed_paths,
                cwd=worktree_path,
            )
        seed_status = capture(
            ["git", "status", "--porcelain", "--"] + seed_paths,
            cwd=worktree_path,
            check=False,
        ) if seed_paths else ""
        if seed_status:
            run(
                ["git", "add"] + seed_paths,
                cwd=worktree_path,
            )
            run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"Seed {design_rel} rtl/model from {seed_ref} for {run_name}",
                ],
                cwd=worktree_path,
            )

    if not git_status_is_clean(worktree_path):
        raise SystemExit(
            f"worktree is dirty: {worktree_path}\n"
            "finish or clean that run before launching more experiments"
        )

    return created


def write_manifest(
    manifest_path: Path,
    run_name: str,
    branch_name: str,
    seed_ref: str,
    design_dir: Path,
    worktree_path: Path,
    artifacts_dir: Path,
    args: argparse.Namespace,
) -> None:
    payload = {
        "run_name": run_name,
        "branch_name": branch_name,
        "seed_ref": seed_ref,
        "design_dir": str(design_dir),
        "worktree_path": str(worktree_path),
        "artifacts_dir": str(artifacts_dir),
        "mode": args.mode,
        "agent_cli": args.agent_cli,
        "agent_model": args.agent_model,
        "max_experiments": args.max_experiments,
        "dry_run": args.dry_run,
        "launcher": str(Path(__file__).resolve()),
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_harness_cmd(
    design_dir: Path,
    artifacts_dir: Path,
    args: argparse.Namespace,
) -> list[str]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "harness" / "autosilicon.py"),
        "--mode",
        args.mode,
        "--design-dir",
        str(design_dir),
        "--artifacts-dir",
        str(artifacts_dir),
        "--agent-cli",
        args.agent_cli,
        "--agent-model",
        args.agent_model,
        "--max-experiments",
        str(args.max_experiments),
    ]
    if args.timeout_per_run is not None:
        cmd.extend(["--timeout-per-run", str(args.timeout_per_run)])
    if args.agent_timeout is not None:
        cmd.extend(["--agent-timeout", str(args.agent_timeout)])
    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def main() -> int:
    args = parse_args()
    design_dir, design_rel = resolve_design_dir(args.design_dir)
    seed_ref = args.seed_ref or discover_seed_ref(design_dir)
    run_name = args.run_name or f"{slugify(args.agent_model)}-seed-r1"
    branch_name = f"codex/{design_dir.name}-{run_name}"
    worktree_root = (
        args.worktree_root.resolve()
        if args.worktree_root.is_absolute()
        else (REPO_ROOT / args.worktree_root).resolve()
    )
    worktree_path = worktree_root / design_dir.name / run_name
    worktree_design_dir = worktree_path / design_rel
    artifacts_dir = design_dir / "runs" / run_name
    manifest_path = artifacts_dir / "run_manifest.json"

    created = prepare_worktree(worktree_path, branch_name, seed_ref, design_rel, run_name)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(
        manifest_path,
        run_name,
        branch_name,
        seed_ref,
        design_dir,
        worktree_path,
        artifacts_dir,
        args,
    )

    cmd = build_harness_cmd(worktree_design_dir, artifacts_dir, args)
    dashboard_rel = artifacts_dir.relative_to(design_dir)

    print(f"run name:    {run_name}", flush=True)
    print(f"seed ref:    {seed_ref}", flush=True)
    print(f"branch:      {branch_name}", flush=True)
    print(f"worktree:    {worktree_path}", flush=True)
    print(f"artifacts:   {artifacts_dir}", flush=True)
    print(
        f"dashboard:   http://localhost:8765/dashboard.html?artifacts={dashboard_rel.as_posix()}",
        flush=True,
    )
    if created:
        print("workspace:   created fresh from HEAD, then reseeded rtl/model", flush=True)
    else:
        print("workspace:   reusing existing run worktree", flush=True)
    print("command:     " + " ".join(shlex.quote(part) for part in cmd), flush=True)
    print(flush=True)

    proc = subprocess.Popen(cmd, cwd=REPO_ROOT, start_new_session=True)
    _active_proc = proc
    proc.wait()
    _active_proc = None
    return proc.returncode


_active_proc = None

if __name__ == "__main__":
    import signal as _signal

    def _die(signum, _frame):
        proc = _active_proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
            except OSError:
                pass
        sys.exit(130)

    _signal.signal(_signal.SIGINT, _die)
    _signal.signal(_signal.SIGQUIT, _die)
    sys.exit(main())
