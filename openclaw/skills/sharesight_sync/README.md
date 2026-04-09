# sharesight_sync

Read payout fields from a configured Excel worksheet and update **unconfirmed** Sharesight payouts through the API (OAuth2 client credentials).

| | |
| --- | --- |
| **Entry** | `SharesightSyncSkill` in `skill.py` |
| **Config** | `config.yaml` |
| **Secrets** | `SHARESIGHT_CLIENT_ID` / `SHARESIGHT_CLIENT_SECRET` (names overridable in config) |

## Configuration highlights

- `portfolio_name`, `worksheet_name` — target portfolio and sheet
- `excel_path`, `excel_path_env_var` — workbook (default env `OPENCLAW_SHARESIGHT_SYNC_EXCEL_PATH`)
- `client_id_env`, `client_secret_env` — env var names for OAuth
- `dry_run` — avoid API writes when enabled
- `tax_field_name`, `confirmed_state`, `unconfirmed_state`
- `update_existing_payouts_by_id`, optional `payouts_start_date` / `payouts_end_date`

## Workbook path (order of precedence)

1. `excel_path=` argument to `SharesightSyncSkill(...)`
2. Environment variable from `excel_path_env_var`
3. `excel_path` in `config.yaml`

## CLI example

```bash
cd /path/to/OpenClaw
source .venv/bin/activate

export SHARESIGHT_CLIENT_ID="..."
export SHARESIGHT_CLIENT_SECRET="..."
export OPENCLAW_SHARESIGHT_SYNC_EXCEL_PATH="/absolute/path/to/workbook.xlsx"

PYTHONPATH=/path/to/OpenClaw \
python -c "from openclaw.skills.sharesight_sync.skill import SharesightSyncSkill; print(SharesightSyncSkill(config_path='openclaw/skills/sharesight_sync/config.yaml').run())"
```

## Dependencies

See imports in `skill.py` (includes `openpyxl`); install into the same venv you use to run the skill.
