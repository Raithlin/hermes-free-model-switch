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
SNAPSHOT_DIR = os.path.join(HERMES_HOME, "model-snapshots")
WIZARD_STATE_PATH = os.path.join(SNAPSHOT_DIR, "wizard-state.json")

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
# Wizard state machine
# ---------------------------------------------------------------------------

WIZARD_SCHEMA = {
    "model": "1/3: Which model? (e.g., stepfun/step-3.7-flash:free)",
    "provider": "2/3: Which provider? (nous, openrouter, deepseek, ...) [default]",
    "scope": "3/3: Scope? (full / gateway-only) [full]",
    "confirm": None,  # handled specially
}


def _wizard_state() -> dict | None:
    """Read current wizard state, or None if no wizard is active."""
    if not os.path.exists(WIZARD_STATE_PATH):
        return None
    try:
        with open(WIZARD_STATE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _wizard_save(state: dict) -> None:
    """Persist wizard state."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(WIZARD_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _wizard_clear() -> None:
    """Remove wizard state."""
    try:
        os.remove(WIZARD_STATE_PATH)
    except OSError:
        pass


def _wizard_start() -> str:
    """Initialize a new wizard and return the first question."""
    state = {
        "created_at": datetime.now().isoformat(),
        "step": "model",
        "model": None,
        "provider": None,
        "scope": None,
    }
    _wizard_save(state)
    return (
        "\n"
        "  ┌─ Free Model Switch ─────────────────────────────┐\n"
        "  │                                                  │\n"
        f"  │  {WIZARD_SCHEMA['model'].ljust(49)}│\n"
        "  │                                                  │\n"
        "  └──────────────────────────────────────────────────┘\n"
        "\n"
        f"Type:  /free-model <answer>"
    )


def _wizard_advance(state: dict, answer: str) -> str:
    """Apply the user's answer to the current step and advance to the next.

    Returns the next question or the execution result on completion.
    """
    step = state["step"]

    if step == "model":
        err = _validate_model(answer)
        if err:
            return f"Invalid model: {err}\nType /free-model with a valid model name."
        state["model"] = answer
        default_provider = _get_current_provider() or "openrouter"
        state["provider"] = default_provider  # default until user overrides
        state["step"] = "provider"
        _wizard_save(state)
        return (
            f"  ✓ Model: {answer}\n\n"
            f"  {WIZARD_SCHEMA['provider']}\n"
            f"     (Enter for default: {default_provider})"
        )

    elif step == "provider":
        if answer:
            state["provider"] = answer
        state["step"] = "scope"
        _wizard_save(state)
        return (
            f"  ✓ Provider: {state['provider']}\n\n"
            f"  {WIZARD_SCHEMA['scope']}"
        )

    elif step == "scope":
        scope = answer.strip().lower()
        if scope in ("gateway-only", "gateway", "g"):
            state["scope"] = "gateway-only"
        else:
            state["scope"] = "full"
        state["step"] = "confirm"
        _wizard_save(state)
        model = state["model"]
        provider = state["provider"]
        scope_label = state["scope"]
        return (
            "  ┌─ Free Model Switch ─────────────────────────────┐\n"
            f"  │  ✓ Model:    {model.ljust(38)}│\n"
            f"  │  ✓ Provider: {provider.ljust(38)}│\n"
            f"  │  ✓ Scope:    {scope_label.ljust(38)}│\n"
            "  │                                                  │\n"
            "  │  Apply? (yes/no) [yes]                             │\n"
            "  └──────────────────────────────────────────────────┘\n"
            "\n"
            "Type:  /free-model yes   — to apply\n"
            "       /free-model no    — to cancel"
        )

    elif step == "confirm":
        if answer.lower() in ("y", "yes", ""):
            return _wizard_execute(state)
        else:
            _wizard_clear()
            return "Cancelled."

    return "Wizard step not recognized. Start over: /free-model"


def _wizard_execute(state: dict) -> str:
    """Execute the switch from wizard state and clean up."""
    model = state["model"]
    provider = state["provider"]
    gateway_only = state["scope"] == "gateway-only"

    set_model_args = ["--model", model, "--provider", provider]
    if gateway_only:
        set_model_args.extend(["--skip-delegation", "--skip-cron"])

    _wizard_clear()
    output = _run_set_model(set_model_args)
    return "✓ Applied.\n\n" + output


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_free_model(raw_args: str) -> Optional[str]:
    args = raw_args.strip()

    # --- Active wizard: advance with user's answer ---
    wizard = _wizard_state()
    if wizard and args:
        return _wizard_advance(wizard, args)

    # --- Active wizard, no args: show progress ---
    if wizard and not args:
        step = wizard["step"]
        model = wizard.get("model") or "—"
        provider = wizard.get("provider") or "—"
        scope = wizard.get("scope") or "—"
        return (
            "\n"
            "  ┌─ Free Model Switch (in progress) ───────────────┐\n"
            f"  │  Model:    {model.ljust(39)}│\n"
            f"  │  Provider: {provider.ljust(39)}│\n"
            f"  │  Scope:    {scope.ljust(39)}│\n"
            "  │                                                  │\n"
            f"  │  {WIZARD_SCHEMA.get(step, 'Done.').ljust(49)}│\n"
            "  │                                                  │\n"
            "  └──────────────────────────────────────────────────┘\n"
            "\n"
            "Type:  /free-model <answer>     — to provide the info above\n"
            "       /free-model cancel       — to cancel the wizard"
        )

    # --- No active wizard, no args: start wizard ---
    if not args:
        return _wizard_start()

    # --- No active wizard, with args: handle as cancel or one-shot ---
    if args.lower().strip() == "cancel":
        _wizard_clear()
        return "Cancelled."

    # --- One-shot command (existing behavior) ---
    parts = args.split()
    model = parts[0]

    err = _validate_model(model)
    if err:
        return f"Invalid model: {err}"

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

    return _run_set_model(set_model_args)


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
