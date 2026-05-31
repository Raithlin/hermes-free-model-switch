#!/usr/bin/env python3
"""
Set the active model across all Hermes surfaces:
  - Config.yaml (model.default + delegation.model) → gateway + kanban workers + subagents
  - Cron jobs (explicit model pins in jobs.json)

Usage:
  # Switch everything to a free model
  python3 set-model.py --model arcee-ai/trinity-mini:free --provider openrouter

  # Revert to the most recent snapshot
  python3 set-model.py --revert

  # Preview changes without writing
  python3 set-model.py --model ... --dry-run
"""

import argparse
import json
import os
import shutil
import sys
import yaml
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
JOBS_PATH = os.path.join(HERMES_HOME, "cron", "jobs.json")
SNAPSHOT_DIR = os.path.join(HERMES_HOME, "model-snapshots")
MAX_SNAPSHOT_AGE_DAYS = 30
REVERT_STACK_SIZE = 5


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_model(model: str) -> str:
    """Basic sanity check on model string. Returns error or empty string."""
    if " " in model:
        return "Model name must not contain spaces."
    if ":" not in model and "/" in model:
        # Likely fine — org/model without suffix
        return ""
    if "/" not in model:
        return "Model name should include provider prefix (e.g., org/model)."
    return ""


# ---------------------------------------------------------------------------
# Snapshot management
# ---------------------------------------------------------------------------

def backup_file(path):
    """Create a dated snapshot copy of a file. Returns the snapshot path."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    name = os.path.basename(path)
    dst = os.path.join(SNAPSHOT_DIR, f"{name}.{ts}")
    shutil.copy2(path, dst)
    return dst


def _prune_old_snapshots():
    """Remove snapshots older than MAX_SNAPSHOT_AGE_DAYS."""
    if not os.path.isdir(SNAPSHOT_DIR):
        return
    cutoff = datetime.now() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
    for fn in os.listdir(SNAPSHOT_DIR):
        if fn == "revert-manifest.json":
            continue
        fp = os.path.join(SNAPSHOT_DIR, fn)
        try:
            mtime = os.path.getmtime(fp)
            if datetime.fromtimestamp(mtime) < cutoff:
                os.remove(fp)
        except OSError:
            pass


def load_revert_stack() -> list[dict]:
    """Load the ordered revert stack. Oldest entry first."""
    manifest_path = os.path.join(SNAPSHOT_DIR, "revert-manifest.json")
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "stack" in data:
        return data["stack"]
    # Legacy format — single snapshot entry
    if isinstance(data, dict) and "files" in data:
        return [{"files": data["files"]}]
    return []


def _save_revert_stack(stack: list[dict]):
    """Persist the revert stack."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    manifest_path = os.path.join(SNAPSHOT_DIR, "revert-manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({"stack": stack}, f, indent=2)


def push_revert_entry(config_bak: str, jobs_bak: str):
    """Push a new snapshot pair onto the revert stack and prune old ones.

    Maintains REVERT_STACK_SIZE entries so you can chain reverts.
    """
    stack = load_revert_stack()
    entry = {
        "created_at": datetime.now().isoformat(),
        "files": {
            os.path.relpath(CONFIG_PATH, HERMES_HOME): config_bak,
            os.path.relpath(JOBS_PATH, HERMES_HOME): jobs_bak,
        },
    }
    stack.append(entry)
    # Keep only the most recent N entries
    if len(stack) > REVERT_STACK_SIZE:
        # Remove snapshots of the dropped entry from disk
        for stale in stack[:-REVERT_STACK_SIZE]:
            _remove_snapshot_files(stale)
        stack = stack[-REVERT_STACK_SIZE:]
    _save_revert_stack(stack)
    _prune_old_snapshots()


def _remove_snapshot_files(entry: dict):
    """Delete snapshot files referenced by a manifest entry from disk."""
    for snapshot in (entry.get("files") or {}).values():
        if os.path.exists(snapshot):
            try:
                os.remove(snapshot)
            except OSError:
                pass


def pop_revert_entry() -> dict | None:
    """Pop the most recent revert entry. Returns entry or None if empty."""
    stack = load_revert_stack()
    if not stack:
        return None
    entry = stack.pop()
    if stack:
        _save_revert_stack(stack)
    else:
        mf = os.path.join(SNAPSHOT_DIR, "revert-manifest.json")
        try:
            os.remove(mf)
        except OSError:
            pass
    return entry


def revert_entry_count() -> int:
    return len(load_revert_stack())


# ---------------------------------------------------------------------------
# Config updaters
# ---------------------------------------------------------------------------

def update_config_yaml(model, provider, base_url, dry_run=False, skip_delegation=False):
    """Update model.default and delegation.model in config.yaml."""
    config = load_yaml(CONFIG_PATH)
    changed = []

    # --- model.default ---
    old_default = config.get("model", {}).get("default")
    if old_default != model:
        config.setdefault("model", {})["default"] = model
        changed.append(f"  model.default: {old_default or '(unset)'} → {model}")

    # --- model.provider ---
    if provider:
        old_prov = config.get("model", {}).get("provider")
        if old_prov != provider:
            config.setdefault("model", {})["provider"] = provider
            changed.append(f"  model.provider: {old_prov or '(unset)'} → {provider}")

    # --- model.base_url ---
    if base_url:
        old_url = config.get("model", {}).get("base_url")
        if old_url != base_url:
            config.setdefault("model", {})["base_url"] = base_url
            changed.append(f"  model.base_url: {old_url or '(unset)'} → {base_url}")

    # --- delegation (skipped if --skip-delegation or --gateway-only) ---
    if not skip_delegation:
        old_del_model = config.get("delegation", {}).get("model")
        if old_del_model != model:
            config.setdefault("delegation", {})["model"] = model
            changed.append(f"  delegation.model: {old_del_model or '(unset)'} → {model}")

    if provider and not skip_delegation:
        old_del_prov = config.get("delegation", {}).get("provider")
        if old_del_prov != provider:
            config.setdefault("delegation", {})["provider"] = provider
            changed.append(f"  delegation.provider: {old_del_prov or '(unset)'} → {provider}")

    if not dry_run and changed:
        write_yaml(CONFIG_PATH, config)

    return changed


def update_cron_jobs(model, provider, base_url, dry_run=False):
    """Update model/provider on LLM-driven cron jobs. Skip no_agent jobs."""
    data = load_json(JOBS_PATH)
    jobs = data["jobs"]
    changed = []

    for job in jobs:
        if job.get("no_agent"):
            continue

        old_model = job.get("model")
        old_prov = job.get("provider")

        if old_model != model:
            job["model"] = model
            changed.append(f"  cron/{job.get('name', job['id'])}.model: {old_model or '(inherit)'} → {model}")

        if provider and old_prov != provider:
            job["provider"] = provider
            changed.append(f"  cron/{job.get('name', job['id'])}.provider: {old_prov or '(inherit)'} → {provider}")

        if base_url:
            job["base_url"] = base_url

    if not dry_run and changed:
        write_json(JOBS_PATH, data)

    return changed


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def do_switch(model, provider, base_url, skip_config, skip_delegation, skip_cron, dry_run):
    """Switch everything to the given model, with rollback on failure."""
    # Validate first
    err = validate_model(model)
    if err:
        print(f"ERROR: {err}")
        sys.exit(1)

    print(f"[hermes set-model] Switching to: {model}")
    if provider:
        print(f"  provider: {provider}")
    if base_url:
        print(f"  base_url: {base_url}")
    scopes = []
    if not skip_config:
        scopes.append("gateway")
    if not skip_delegation:
        scopes.append("delegation")
    if not skip_cron:
        scopes.append("cron")
    print(f"  scope: {', '.join(scopes)}")
    if dry_run:
        print("  DRY RUN — no changes written")
    print()

    # Backup before making changes
    config_bak = backup_file(CONFIG_PATH)
    jobs_bak = backup_file(JOBS_PATH)

    changes = []

    try:
        if not skip_config:
            changes.extend(update_config_yaml(model, provider, base_url, dry_run, skip_delegation=skip_delegation))

        if not skip_cron:
            changes.extend(update_cron_jobs(model, provider, base_url, dry_run))
    except Exception as exc:
        # Rollback: restore from backups
        print(f"ERROR during write: {exc}")
        print("Rolling back...")
        shutil.copy2(config_bak, CONFIG_PATH)
        shutil.copy2(jobs_bak, JOBS_PATH)
        os.remove(config_bak)
        os.remove(jobs_bak)
        print("Rollback complete. No changes written.")
        sys.exit(1)

    if not changes:
        print("No changes needed — everything already matches.")
        # Clean up unused backups
        os.remove(config_bak)
        os.remove(jobs_bak)
        return

    print("Changes:")
    for c in changes:
        print(c)

    if not dry_run:
        push_revert_entry(config_bak, jobs_bak)
        depth = revert_entry_count()
        print(f"\nSnapshot saved — you can revert with --revert")
        if depth > 1:
            print(f"  ({depth} previous snapshots available for chained revert)")
        print(f"\nRevert with: {os.path.abspath(__file__)} --revert")
        print()
        print("To schedule automatic revert (e.g. daily at 23:00):")
        print(f'  hermes cron create "0 23 * * *" \\')
        print(f'    --name "revert-free-model" \\')
        print(f'    --prompt "Run: {os.path.abspath(__file__)} --revert" \\')
        print(f'    --deliver local')
    else:
        print("\n(DRY RUN — reverted backups)")
        shutil.copy2(config_bak, CONFIG_PATH)
        shutil.copy2(jobs_bak, JOBS_PATH)
        os.remove(config_bak)
        os.remove(jobs_bak)


def do_revert(dry_run):
    """Restore the most recent snapshot from the stack."""
    entry = pop_revert_entry()
    if not entry:
        print("ERROR: No revert snapshots found. Run with --model first.")
        sys.exit(1)

    files = entry["files"]
    remaining = revert_entry_count()

    print(f"[hermes set-model] Reverting to snapshot from {entry['created_at']}")
    if remaining > 0:
        print(f"  ({remaining} earlier snapshots still available for further revert)")
    print()

    restored = []
    for target, snapshot in files.items():
        if not os.path.exists(snapshot):
            print(f"  WARNING: snapshot missing: {snapshot}")
            continue
        target_path = os.path.join(HERMES_HOME, target) if not os.path.isabs(target) else target
        if not dry_run:
            shutil.copy2(snapshot, target_path)
            os.remove(snapshot)
        restored.append(f"  {target} → restored from {snapshot}")

    for r in restored:
        print(r)

    if not dry_run:
        if remaining == 0:
            print("\nRevert complete. No more snapshots to revert to.")
        else:
            print(f"\nRevert complete. {remaining} earlier snapshot(s) remain — run --revert again to go farther back.")
    else:
        print("\n(DRY RUN — no changes written)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Switch all Hermes surfaces to a specific model"
    )
    parser.add_argument("--model", help="Model name (e.g. arcee-ai/trinity-mini:free)")
    parser.add_argument("--provider", help="Provider (e.g. openrouter, deepseek)")
    parser.add_argument("--base-url", help="Base URL override")
    parser.add_argument("--skip-config", action="store_true", help="Don't update config.yaml")
    parser.add_argument("--skip-delegation", action="store_true", help="Don't update delegation.model")
    parser.add_argument("--skip-cron", action="store_true", help="Don't update cron jobs")
    parser.add_argument("--gateway-only", action="store_true", help="Shorthand: skip delegation + cron, only set gateway config")
    parser.add_argument("--revert", action="store_true", help="Revert to last snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")

    args = parser.parse_args()

    if args.revert:
        do_revert(args.dry_run)
        return

    if not args.model:
        parser.print_help()
        print("\nERROR: --model is required (or use --revert)")
        sys.exit(1)

    # --gateway-only is shorthand for --skip-delegation --skip-cron
    skip_delegation = args.skip_delegation or args.gateway_only
    skip_cron = args.skip_cron or args.gateway_only

    do_switch(args.model, args.provider, args.base_url,
              args.skip_config, skip_delegation, skip_cron, args.dry_run)


if __name__ == "__main__":
    main()
