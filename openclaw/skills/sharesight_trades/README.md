# sharesight_trades

Reconcile Sharesight `CAPITAL_RETURN` trades with Excel on `CS FY2526`. The **spreadsheet is the source of truth**: add missing trades, update mismatched ROC or exchange rate, delete trades that are not on the sheet (for the listed date window).

| | |
| --- | --- |
| **Entry** | `SharesightTradesSkill` in `skill.py` |
| **Config** | `config.yaml` |
| **Secrets** | `SHARESIGHT_CLIENT_ID` / `SHARESIGHT_CLIENT_SECRET` |

## Worksheet mapping

- `trade/transaction_date` -> column `H` (`Pay Date`)
- `trade/paid_on` -> column `H` (`Pay Date`)
- `trade/price` and `trade/capital_return_value` -> column `L` (`ROC $`)
- `trade/exchange_rate` -> column `T` (`Exchange Rate`)
- `trade/comments` -> `"{ROC%}% {Pay Date short} ROC. Gross Amt ${Gross Amt}"`
  - Example: `94.69% 23-Jan ROC. Gross Amt $571.47`

All managed trades are `transaction_type: CAPITAL_RETURN` for the configured `holding_id`. Rows with ROC ≤ 0 or exchange rate ≤ 0 are skipped (not synced).

## Reconcile rules

1. **List trades** — `GET /portfolios/{portfolio_id}/trades.json` with `start_date` / `end_date` = min and max **Pay Date** on the sheet (any row with a date).
2. **Scope** — Only trades with `holding_id` = config and `CAPITAL_RETURN` are reconciled.
3. **Delete** — Such a trade whose **transaction_date** is not a Pay Date on any worksheet row → `DELETE /trades/{id}.json`. Duplicate trades on the same Pay Date (same holding/type): keep the trade with the **lowest id**, delete the rest.
4. **Update** — One primary trade on a Pay Date that matches the sheet but **ROC** or **exchange_rate** differs (within float epsilon `1e-6`) → `PUT /trades/{id}.json`.
5. **Add** — Valid sheet row for a Pay Date with no primary trade → `POST /trades.json` (confirmed create).

Trades outside the sheet’s min–max Pay Date range are **not** returned by that list call, so they are not considered for delete/update in that run.

## Dry run

With `dry_run: true` (or `run(dry_run=True)`), the skill still calls **GET** to list trades but does **not** POST, PUT, or DELETE.

Result highlights:

- `reconcile_add`, `reconcile_update`, `reconcile_delete`, `reconcile_noop` — planned actions with payloads where relevant.
- `dry_run_summary` — `to_add_count`, `to_update_count`, `to_delete_count`, `noop_count`.
- Legacy aliases: `dry_run_new_trades` = adds, `dry_run_matches` = noops, `dry_run_payloads` = adds.

Console lines use prefixes `DRY-RUN ADD|UPDATE|DELETE|NOOP` or the same without `DRY-RUN ` when applying.

## Runtime dry-run override

```bash
cd /path/to/OpenClaw/openclaw
source .venv/bin/activate
PYTHONPATH=. \
python -c "from openclaw.skills.sharesight_trades.skill import SharesightTradesSkill; print(SharesightTradesSkill(config_path='skills/sharesight_trades/config.yaml').run(dry_run=True))"
```

If your checkout nests the package (e.g. `OpenClaw/openclaw/` is the inner project), set `PYTHONPATH` to the **parent** directory that contains the `openclaw` package folder, and adjust `config_path` accordingly.

## CLI example

```bash
cd /path/to/OpenClaw/openclaw
source .venv/bin/activate

export SHARESIGHT_CLIENT_ID="..."
export SHARESIGHT_CLIENT_SECRET="..."
export OPENCLAW_SHARESIGHT_SYNC_EXCEL_PATH="/absolute/path/to/workbook.xlsx"

PYTHONPATH=. \
python -c "from openclaw.skills.sharesight_trades.skill import SharesightTradesSkill; print(SharesightTradesSkill(config_path='skills/sharesight_trades/config.yaml').run())"
```

## Dependencies

`openpyxl` (see `skill.py` imports); install into the same venv you use to run the skill.
