# RevenueCat daily metrics (`revenuecat_metrics`)

Pulls overview subscription metrics from RevenueCat's Charts & Metrics API, writes a dated run log to Obsidian (same env-driven path convention as `gmail_triage`), and posts a plain-text summary to Discord.

## Dashlane entries

Store these as Dashlane **Secret** items and reference them from `/etc/openclaw/secrets.env` (or resolve at runtime via `dcli read`):

| Env var | Dashlane item | Field |
|---------|---------------|-------|
| `REVENUECAT_API_KEY` | `RevenueCat API Key` | `password` — use `sk_...` or OAuth `atk_...` with `charts_metrics:overview:read` |
| `REVENUECAT_PROJECT_ID` | `RevenueCat Project ID` | `note` |

Example `secrets.env` lines (resolved by `dcli exec --` on the Pi):

```bash
REVENUECAT_API_KEY=dl://RevenueCat API Key/password
REVENUECAT_PROJECT_ID=dl://RevenueCat Project ID/note
OBSIDIAN_VAULT_PATH=/path/to/vault
SKILL_LOG_SUBFOLDER=OpenClaw/Logs
DISCORD_BOT_TOKEN=...
DISCORD_REVENUECAT_METRICS_CHANNEL_ID=...
```

**Discord:** This repo does not ship the sempiternal bot's channel routing. There is no existing daily-report Discord channel — create a channel (e.g. `#revenuecat-metrics`) and set `DISCORD_REVENUECAT_METRICS_CHANNEL_ID` to its snowflake ID.

## Dry run

Verify formatted output without Obsidian or Discord:

```bash
PYTHONPATH=. REVENUECAT_API_KEY=atk_... REVENUECAT_PROJECT_ID=proj_... \
  python -m openclaw.skills.revenuecat_metrics.revenuecat_metrics_skill --dry-run
```

## Run

```bash
PYTHONPATH=. python -m openclaw.skills.revenuecat_metrics.revenuecat_metrics_skill
```

Or from Python:

```python
from openclaw.skills.revenuecat_metrics import run
result = run()
```

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s openclaw/skills/revenuecat_metrics/tests -p "test_*.py" -v
```
