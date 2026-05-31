# hermes-free-model-switch

Switch all Hermes surfaces (gateway session, delegation, cron jobs) to a temporarily free model, then automatically revert at a scheduled time.

## Quick start

```bash
# Install — copies plugin + scripts, enables the plugin
npx hermes-free-model-switch install

# Or install from source with hermes itself
hermes plugins install npm:hermes-free-model-switch

# Restart the gateway
hermes gateway restart
```

## Usage

### Switch to a free model

```
/free-model stepfun/step-3.7-flash:free
/free-model deepseek/deepseek-v4-flash:free --provider deepseek
/free-model arcee-ai/trinity-mini:free --provider openrouter
```

When `--provider` is omitted, the plugin uses whatever provider is configured in
`model.provider` from your `config.yaml`. Pass `--provider` explicitly when your
configured provider can't serve the model (e.g., a Nous model when your default
provider is OpenRouter).

This switches:
- `model.default` and `delegation.model` in config.yaml
- All LLM-driven cron jobs (skips no_agent jobs)
- Takes a snapshot so you can revert cleanly

### Schedule automatic revert

```
/free-model-end 2026/06/01 23:00
```

This activates a pre-installed one-shot cron job that runs `set-model.py --revert`
at the given time, restoring the previous model everywhere.

### Revert manually

```bash
python3 ~/.hermes/scripts/set-model.py --revert
```

Revert is stack-based — you can revert multiple times to walk back through
previous model states.

## What's installed

| Path | Purpose |
|------|---------|
| `~/.hermes/plugins/free-model/` | Plugin: `/free-model` and `/free-model-end` slash commands |
| `~/.hermes/scripts/set-model.py` | Core model-switching engine |
| `~/.hermes/scripts/revert-model.py` | Wrapper for revert cron |

## Model validation

The plugin validates that the model string:
- Is not empty
- Has no spaces
- Includes a provider prefix (e.g., `org/model:free`)

If something looks wrong, the command returns a clear error before touching config.

## Snapshot management

Every switch creates a snapshot of `config.yaml` and `cron/jobs.json`. Snapshots
are stored in `~/.hermes/model-snapshots/` with a maximum stack depth of 5 —
older entries are pruned. Snapshots older than 30 days are cleaned up
automatically.

If a write fails mid-switch (e.g., disk full, permission error), the plugin
automatically rolls back from the snapshot.

## Requirements

- Hermes Agent v0.15.0+
- Node.js 16+ (only needed for the `npx` installer path)
- Python 3.9+ (for `set-model.py`)

## Environment

The plugin respects `$HERMES_HOME` if set, falling back to `~/.hermes` otherwise.

## License

MIT
