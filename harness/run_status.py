"""Run-status helpers for AutoSilicon live instrumentation."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path


def now_iso() -> str:
    """Return the current local time in ISO-8601 format."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: Path) -> dict:
    """Load a status file, returning an empty dict if unavailable."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write(path: Path, payload: dict) -> dict:
    """Write the status payload as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = now_iso()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def update(path: Path, **fields) -> dict:
    """Merge fields into the current status file and rewrite it."""
    payload = load(path)
    payload.update(fields)
    return write(path, payload)


def git_head(design_dir: Path) -> str:
    """Return the current short git revision, or empty string on failure."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=design_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip()


def git_is_dirty(design_dir: Path) -> bool | None:
    """Return whether the worktree is dirty, or None on failure."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=design_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(proc.stdout.strip())
