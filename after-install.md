## hermes-free-model-switch — Installed ✓

### Slash commands

| Command | What it does |
|---------|-------------|
| `/free-model <model> [--provider <name>]` | Switch all Hermes surfaces (config, delegation, cron) to the given model |
| `/free-model-end yyyy/mm/dd HH:MM` | Schedule automatic revert to the previous model at a specific time |

### Quick start

```text
/free-model deepseek/deepseek-v4-flash:free
/free-model-end 2026/06/15 23:00
```

### What it switches

- `model.default` and `delegation.model` in config.yaml
- All LLM-driven cron jobs (skips no_agent jobs)
- Takes a snapshot so you can revert cleanly

### Files installed

- `~/.hermes/plugins/free-model/` — plugin logic
- `~/.hermes/scripts/set-model.py` — model switching engine
- `~/.hermes/scripts/revert-model.py` — revert wrapper

**Restart the gateway** for changes to take effect:

```text
hermes gateway restart
```
