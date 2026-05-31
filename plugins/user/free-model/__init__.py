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
import yaml
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
SCRIPT_DIR = os.path.join(HERMES_HOME, "scripts")
SET_MODEL_SCRIPT = os.path.join(SCRIPT_DIR, "set-model.py")
REVERT_WRAPPER = os.path.join(SCRIPT_DIR, "revert-model.py")
JOBS_PATH = os.path.join(HERMES_HOME, "cron", "jobs.json")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
CRON_NAME = "revert-free-model"

# Populated by _ensure_revert_cron() at registration time.
_revert_cron_id: Optional[str] = None
_cron_setup_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Plugin registration — creates the sitting cron
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_command(
        "free-model",
        handler=_handle_free_model,
        description="Switch all Hermes surfaces to a free model",
        args_hint="<model> [--provider <name>] [--gateway-only]",
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
    Sets _cron_setup_error on failure so handlers can report it.
    """
    global _revert_cron_id, _cron_setup_error
    _cron_setup_error = None

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
        msg = (result.stderr or result.stdout or "unknown error").strip()
        _cron_setup_error = f"Could not create revert cron: {msg}"
        print(f"[free-model plugin] ERROR: {_cron_setup_error}", file=sys.stderr)
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
# Validation
# ---------------------------------------------------------------------------

def _validate_model(model: str) -> Optional[str]:
    """Basic sanity check on model string. Returns error or None."""
    if not model:
        return "Model name is required."
    if " " in model:
        return "Model name must not contain spaces."
    if "/" not in model:
        return "Model should include provider prefix (e.g., org/model)."
    return None


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def _get_current_provider() -> Optional[str]:
    """Read the current model.provider from config.yaml.

    Fallback: None means caller should raise or error.
    """
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("model", {}) or {}).get("provider")
    except Exception:
        return None


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
            "Run the plugin install/update first."
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
    os.makedirs(SCRIPT_DIR, exist_ok=True)
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

def _prompt(question: str, default: str = "") -> str:
    """Prompt user via terminal with styled question."""
    sys.stdout.write(f"\n  {question} ")
    sys.stdout.flush()
    try:
        ans = sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return default if default else ""
    if not ans and default:
        return default
    return ans


def _wizard() -> str:
    """Interactive wizard for /free-model with no arguments in CLI mode."""
    lines: list[str] = []
    lines.append("")
    lines.append("  ┌─ Free Model Switch ─────────────────────────────┐")

    # Step 1: Model
    lines.append("  │                                                  │")
    default_provider = _get_current_provider()
    while True:
        ans = _prompt("Model (e.g. stepfun/step-3.7-flash:free):")
        if not ans:
            continue
        err = _validate_model(ans)
        if err:
            lines.append(f"  │  ✗ {ans} — {err}")
            continue
        model = ans
        break

    # Step 2: Provider
    prov_default = default_provider or "openrouter"
    ans = _prompt(f"Provider [{prov_default}]:", default=prov_default)
    provider = ans if ans else prov_default

    # Step 3: Scope
    ans = _prompt("Scope: full (all surfaces) or gateway-only [full]:", default="full")
    gateway_only = ans.strip().lower() in ("gateway-only", "gateway", "g")

    # Step 4: Confirm
    lines.append("  │  ✓ Model:    " + model.ljust(38) + "│")
    lines.append("  │  ✓ Provider: " + provider.ljust(38) + "│")
    lines.append("  │  ✓ Scope:    " + ("gateway-only" if gateway_only else "full").ljust(38) + "│")
    lines.append("  │                                                  │")
    confirm = _prompt("Apply? [Y/n]:", default="y")
    if confirm.lower() not in ("y", "", "yes"):
        lines.append("  │  ✗ Cancelled.")
        lines.append("  └──────────────────────────────────────────────────┘")
        lines.append("")
        return "\n".join(lines)

    lines.append("  │  Applying...                                     │")
    lines.append("  └──────────────────────────────────────────────────┘")
    lines.append("")
    sys.stdout.write("\n".join(lines))
    sys.stdout.flush()

    # Execute
    set_model_args = ["--model", model, "--provider", provider]
    if gateway_only:
        set_model_args.extend(["--skip-delegation", "--skip-cron"])

    output = _run_set_model(set_model_args)
    return "\n" + output


def _handle_free_model(raw_args: str) -> Optional[str]:
    args = raw_args.strip()
    if not args:
        # Interactive wizard in TTY mode, help text in gateway/TUI mode
        if sys.stdin.isatty():
            return _wizard()
        return (
            "Usage: /free-model <model> [--provider <name>] [--gateway-only]\n\n"
            "Examples:\n"
            "  /free-model stepfun/step-3.7-flash:free\n"
            "  /free-model deepseek/deepseek-v4-flash:free --provider deepseek\n"
            "  /free-model stepfun/step-3.7-flash:free --gateway-only\n"
            "  /free-model arcee-ai/trinity-mini:free --provider openrouter\n\n"
            "Omitting --provider uses your currently configured default provider.\n"
            "Use --gateway-only to skip delegation model and cron jobs.\n"
            "Switches config.yaml (model.default + delegation.model)\n"
            "and all LLM-driven cron jobs to the given model/provider."
        )

    parts = args.split()
    model = parts[0]

    # Validate model string
    err = _validate_model(model)
    if err:
        return f"Invalid model: {err}"

    # Parse optional flags from remaining args
    flags = parts[1:]
    provider: Optional[str] = None
    gateway_only = False

    i = 0
    while i < len(flags):
        if flags[i] == "--provider" and i + 1 < len(flags):
            provider = flags[i + 1]
            i += 2
        elif flags[i] == "--gateway-only":
            gateway_only = True
            i += 1
        else:
            i += 1

    if not provider:
        provider = _get_current_provider()
        if not provider:
            return (
                "Error: no provider specified and none found in config.yaml.\n"
                "Usage: /free-model <model> --provider <provider>"
            )

    set_model_args = ["--model", model, "--provider", provider]
    if gateway_only:
        set_model_args.extend(["--skip-delegation", "--skip-cron"])

    output = _run_set_model(set_model_args)
    return output


def _handle_free_model_end(raw_args: str) -> Optional[str]:
    args = raw_args.strip()

    # Check for cron setup failures first
    if _cron_setup_error:
        return (
            "Error: revert cron was not set up during plugin load.\n"
            f"{_cron_setup_error}\n\n"
            "Try restarting the gateway to re-initialize the plugin."
        )

    if not args:
        return (
            "Usage: /free-model-end yyyy/mm/dd HH:MM\n\n"
            "Examples:\n"
            "  /free-model-end 2026/06/01 23:00\n"
            "  /free-model-end 2026/06/15\n\n"
            "Schedules the 'revert-free-model' cron to restore the\n"
            "previous model at the specified time."
        )

    # Parse datetime — accept both / and - separators
    dt: Optional[datetime] = None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(args, fmt)
            if fmt in ("%Y/%m/%d", "%Y-%m-%d"):
                dt = dt.replace(hour=0, minute=0)
            break
        except ValueError:
            continue

    if dt is None:
        return (
            f"Could not parse '{args}'.\n"
            "Use format: yyyy/mm/dd HH:MM or yyyy-mm-dd HH:MM\n"
            "  (e.g., 2026/06/01 23:00 or 2026-06-01 23:00)\n"
            "Or just date (defaults to 00:00): 2026/06/15 or 2026-06-15"
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
        f"Cron job '{CRON_NAME}' updated and resumed. "
        f"It will run `set-model.py --revert` at the scheduled time\n"
        f"to restore the previous model."
    )
