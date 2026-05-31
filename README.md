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
/free-model arcee-ai/trinity-mini:free
/free-model deepseek/deepseek-v4-flash:free --provider openrouter
```

This switches:
- `model.default` and `delegation.model` in config.yaml
- All LLM-driven cron jobs (skips no\_agent jobs)
- Takes a snapshot so you can revert cleanly

### Schedule automatic revert

```
/free-model-end 2026/06/01 23:00
```

This activates a pre-installed one-shot cron job that runs `set-model.py --revert` at the given time, restoring the previous model everywhere.

### Revert manually

```bash
python3 ~/.hermes/scripts/set-model.py --revert
```

## What's installed

| Path | Purpose |
|------|---------|
| `~/.hermes/plugins/free-model/` | Plugin: `/free-model` and `/free-model-end` slash commands |
| `~/.hermes/scripts/set-model.py` | Core model-switching engine |
| `~/.hermes/scripts/revert-model.py` | Wrapper for revert cron |

## Requirements

- Hermes Agent v0.15.0+
- Node.js 16+ (only needed for the `npx` installer path)
- Python 3.9+ (for `set-model.py`)

## License

MIT
