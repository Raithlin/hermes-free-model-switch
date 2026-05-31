#!/usr/bin/env python3
"""
Set the active model across all Hermes surfaces:
  - Config.yaml (model.default + delegation.model) → gateway + kanban workers + subagents
  - Cron jobs (explicit model pins in jobs.json)

Usage:
  # Switch everything to a free model
  python3 ~/.hermes/scripts/set-model.py --model arcee-ai/trinity-mini:free --provider openrouter

  # Revert to the previous snapshot
  python3 ~/.hermes/scripts/set-model.py --revert

  # Preview changes without writing
  python3 ~/.hermes/scripts/set-model.py --model ... --dry-run
"""

import argparse
import json
import os
import shutil
import sys
import yaml
from datetime import datetime
from copy import deepcopy

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
JOBS_PATH = os.path.join(HERMES_HOME, "cron", "jobs.json")
SNAPSHOT_DIR = os.path.join(HERMES_HOME, "model-snapshots")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def backup_file(path):
    """Create a snapshot copy of a file."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    name = os.path.basename(path)
    bak = os.path.join(SNAPSHOT_DIR, f"{name}.{ts}")
    shutil.copy2(path, bak)
    return bak


def save_revert_snapshot(config_bak, jobs_bak):
    """Save revert pointers so --revert knows what to restore.

    Files are keyed by relative path from HERMES_HOME so
    shutil.copy2(snapshot, os.path.join(HERMES_HOME, key)) resolves correctly.
    """
    manifest = {
        "created_at": datetime.now().isoformat(),
        "files": {
            os.path.relpath(CONFIG_PATH, HERMES_HOME): config_bak,
            os.path.relpath(JOBS_PATH, HERMES_HOME): jobs_bak,
        },
    }
    manifest_path = os.path.join(SNAPSHOT_DIR, "revert-manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def load_revert_snapshot():
    manifest_path = os.path.join(SNAPSHOT_DIR, "revert-manifest.json")
    if not os.path.exists(manifest_path):
        print("ERROR: No revert snapshot found. Run with --model first.")
        sys.exit(1)
    with open(manifest_path) as f:
        return json.load(f)


def update_config_yaml(model, provider, base_url, dry_run=False):
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

    # --- delegation.model ---
    old_del_model = config.get("delegation", {}).get("model")
    if old_del_model != model:
        config.setdefault("delegation", {})["model"] = model
        changed.append(f"  delegation.model: {old_del_model or '(unset)'} → {model}")

    # --- delegation.provider ---
    if provider:
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
        # Skip no_agent jobs — they don't use LLMs
        if job.get("no_agent"):
            continue

        old_model = job.get("model")
        old_prov = job.get("provider")

        # Always set model (even if already set, to ensure consistency)
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


def do_switch(model, provider, base_url, skip_config, skip_cron, dry_run):
    """Switch everything to the given model."""
    print(f"[hermes set-model] Switching to: {model}")
    if provider:
        print(f"  provider: {provider}")
    if base_url:
        print(f"  base_url: {base_url}")
    if dry_run:
        print("  DRY RUN — no changes written")
    print()

    # Backup before making changes
    config_bak = backup_file(CONFIG_PATH)
    jobs_bak = backup_file(JOBS_PATH)

    changes = []

    if not skip_config:
        changes.extend(update_config_yaml(model, provider, base_url, dry_run))

    if not skip_cron:
        changes.extend(update_cron_jobs(model, provider, base_url, dry_run))

    if not changes:
        print("No changes needed — everything already matches.")
        return

    print("Changes:")
    for c in changes:
        print(c)

    if not dry_run:
        save_revert_snapshot(config_bak, jobs_bak)
        print(f"\nSnapshots saved to: {SNAPSHOT_DIR}/")
        print(f"  config.yaml → {config_bak}")
        print(f"  jobs.json → {jobs_bak}")
        print("\nRevert with: python3 ~/.hermes/scripts/set-model.py --revert")
        print()
        print("To schedule automatic revert (e.g. daily at 23:00):")
        print(f'  hermes cron create "0 23 * * *" \\')
        print(f'    --name "revert-free-model" \\')
        print(f'    --prompt "Run: python3 {os.path.abspath(__file__)} --revert" \\')
        print(f'    --deliver local')
    else:
        print("\n(DRY RUN — reverted backups)")
        # Restore backups since dry run
        shutil.copy2(config_bak, CONFIG_PATH)
        shutil.copy2(jobs_bak, JOBS_PATH)
        os.remove(config_bak)
        os.remove(jobs_bak)


def do_revert(dry_run):
    """Restore the most recent snapshot."""
    manifest = load_revert_snapshot()
    files = manifest["files"]

    print(f"[hermes set-model] Reverting to snapshot from {manifest['created_at']}")
    print()

    restored = []
    for target, snapshot in files.items():
        if not os.path.exists(snapshot):
            print(f"  WARNING: snapshot missing: {snapshot}")
            continue
        target_path = os.path.join(HERMES_HOME, target) if not os.path.isabs(target) else target
        if not dry_run:
            shutil.copy2(snapshot, target_path)
        restored.append(f"  {target} → restored from {snapshot}")

    for r in restored:
        print(r)

    if not dry_run:
        os.remove(os.path.join(SNAPSHOT_DIR, "revert-manifest.json"))
        print("\nRevert complete. Snapshot manifest removed.")
    else:
        print("\n(DRY RUN — no changes written)")


def main():
    parser = argparse.ArgumentParser(
        description="Switch all Hermes surfaces to a specific model"
    )
    parser.add_argument("--model", help="Model name (e.g. arcee-ai/trinity-mini:free)")
    parser.add_argument("--provider", help="Provider (e.g. openrouter, deepseek)")
    parser.add_argument("--base-url", help="Base URL override")
    parser.add_argument("--skip-config", action="store_true", help="Don't update config.yaml")
    parser.add_argument("--skip-cron", action="store_true", help="Don't update cron jobs")
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

    do_switch(args.model, args.provider, args.base_url,
              args.skip_config, args.skip_cron, args.dry_run)


if __name__ == "__main__":
    main()
