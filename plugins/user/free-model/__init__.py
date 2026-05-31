"""
Hermes plugin: /free-model and /free-model-end slash commands.

On install, creates a paused revert cron that just sits there.
/free-model switches all surfaces to a free model.
/free-model-end sets the revert time and resumes the cron.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.expanduser("~/.hermes/scripts")
SET_MODEL_SCRIPT = os.path.join(SCRIPT_DIR, "set-model.py")
REVERT_WRAPPER = os.path.join(SCRIPT_DIR, "revert-model.py")
JOBS_PATH = os.path.expanduser("~/.hermes/cron/jobs.json")
CRON_NAME = "revert-free-model"
DEFAULT_PROVIDER = "openrouter"

# Populated by _ensure_revert_cron() at registration time.
_revert_cron_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Plugin registration — creates the sitting cron
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_command(
        "free-model",
        handler=_handle_free_model,
        description="Switch all Hermes surfaces to a free model",
        args_hint="<model> [--provider <name>]",
    )
    ctx.register_command(
        "free-model-end",
        handler=_handle_free_model_end,
        description="Schedule automatic revert to previous model",
        args_hint="yyyy/mm/dd HH:MM",
    )
    _ensure_revert_cron()


def _ensure_revert_cron() -> None:
    """Create the revert cron job if missing, then ensure it's paused.

    Idempotent — safe to call on every plugin load.
    """
    global _revert_cron_id

    # Ensure the revert wrapper script exists
    _create_revert_wrapper()

    # Check if cron already exists
    existing = _find_cron_by_name(CRON_NAME)
    if existing:
        _revert_cron_id = existing["id"]
        return

    # Create with a far-future placeholder schedule — never fires as-is.
    result = subprocess.run(
        [
            "hermes", "cron", "create",
            "2100-01-01T00:00:00",
            "--name", CRON_NAME,
            "--script", "revert-model.py",
            "--no-agent",
            "--deliver", "local",
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode:
        # Non-fatal — log but don't crash plugin load.
        msg = (result.stderr or result.stdout or "unknown error").strip()
        print(f"[free-model plugin] WARNING: could not create revert cron: {msg}",
              file=sys.stderr)
        return

    # Find ID from the created job
    created = _find_cron_by_name(CRON_NAME)
    if created:
        _revert_cron_id = created["id"]

    # Pause so it sits idle until /free-model-end schedules it
    if _revert_cron_id:
        subprocess.run(
            ["hermes", "cron", "pause", _revert_cron_id],
            capture_output=True, timeout=15,
        )


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------

def _find_cron_by_name(name: str) -> Optional[dict]:
    """Return the cron job dict for the given name, or None."""
    try:
        with open(JOBS_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for job in data.get("jobs", []):
        if job.get("name") == name:
            return job
    return None


def _update_cron_schedule(job_id: str, iso: str) -> str:
    """Update a cron job's schedule via CLI. Returns error string or empty."""
    result = subprocess.run(
        ["hermes", "cron", "edit", job_id, "--schedule", iso],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        return (result.stderr or result.stdout or "").strip()
    return ""


# ---------------------------------------------------------------------------
# Script helpers
# ---------------------------------------------------------------------------

def _run_set_model(args: list[str]) -> str:
    """Run set-model.py and return combined output."""
    try:
        result = subprocess.run(
            [sys.executable, SET_MODEL_SCRIPT] + args,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return (
            "Error: set-model.py not found at:\n"
            f"  {SET_MODEL_SCRIPT}\n"
            "Run the model-switch skill setup first."
        )
    except subprocess.TimeoutExpired:
        return "Error: set-model.py timed out after 30 seconds."

    output = result.stdout.strip()
    if result.returncode:
        if result.stderr:
            output += "\n" + result.stderr.strip()
        output += f"\n[exit code {result.returncode}]"
    return output


def _create_revert_wrapper() -> None:
    """Create revert-model.py wrapper script if missing."""
    if os.path.exists(REVERT_WRAPPER):
        return
    wrapper = '''#!/usr/bin/env python3
"""Wrapper that reverts model config — invoked by revert-free-model cron job."""
import os, subprocess, sys

script = os.path.join(os.path.dirname(__file__), "set-model.py")
result = subprocess.run(
    [sys.executable, script, "--revert"],
    capture_output=True, text=True, timeout=30,
)
sys.stdout.write(result.stdout)
if result.returncode:
    sys.stderr.write(result.stderr)
    sys.exit(result.returncode)
'''
    with open(REVERT_WRAPPER, "w") as f:
        f.write(wrapper)
    os.chmod(REVERT_WRAPPER, 0o755)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_free_model(raw_args: str) -> Optional[str]:
    args = raw_args.strip()
    if not args:
        return (
            "Usage: /free-model <model> [--provider <name>]\n\n"
            "Examples:\n"
            "  /free-model deepseek/deepseek-v4-flash:free\n"
            "  /free-model arcee-ai/trinity-mini:free --provider openrouter\n\n"
            "Switches config.yaml (model.default + delegation.model)\n"
            "and all LLM-driven cron jobs to the given model."
        )

    parts = args.split()
    model = parts[0]
    provider = DEFAULT_PROVIDER

    if len(parts) >= 3 and parts[1] == "--provider":
        provider = parts[2]

    output = _run_set_model(["--model", model, "--provider", provider])
    return output


def _handle_free_model_end(raw_args: str) -> Optional[str]:
    args = raw_args.strip()
    if not args:
        return (
            "Usage: /free-model-end yyyy/mm/dd HH:MM\n\n"
            "Examples:\n"
            "  /free-model-end 2026/06/01 23:00\n"
            "  /free-model-end 2026/06/15\n\n"
            "Schedules the 'revert-free-model' cron to restore the\n"
            "previous model at the specified time."
        )

    # Parse datetime
    dt: Optional[datetime] = None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(args, fmt)
            if fmt == "%Y/%m/%d":
                dt = dt.replace(hour=0, minute=0)
            break
        except ValueError:
            continue

    if dt is None:
        return (
            f"Could not parse '{args}'.\n"
            "Use format: yyyy/mm/dd HH:MM (e.g., 2026/06/01 23:00)\n"
            "Or just: yyyy/mm/dd (defaults to 00:00)"
        )

    if dt < datetime.now():
        return "Error: the specified time is in the past."

    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")

    # Look up the revert cron — may have been created by a previous load
    job = _find_cron_by_name(CRON_NAME)
    if not job:
        return (
            "Error: revert cron job not found.\n"
            "Try running /free-model first, or restart the gateway\n"
            "to trigger plugin re-registration."
        )

    job_id = job["id"]

    # Update schedule
    err = _update_cron_schedule(job_id, iso)
    if err:
        return f"Failed to update cron schedule: {err}"

    # Resume the cron (it was paused at creation)
    subprocess.run(
        ["hermes", "cron", "resume", job_id],
        capture_output=True, timeout=15,
    )

    return (
        f"Revert scheduled for {dt.strftime('%Y-%m-%d %H:%M')}.\n"
        f"Cron job 'revert-free-model' updated and resumed. "
        f"It will run `set-model.py --revert` at the scheduled time\n"
        f"to restore the previous model."
    )
