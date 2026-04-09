# sharesight_trades

Create Sharesight `CAPITAL_RETURN` trades as `confirmed` from Excel rows in `CS FY2526`.

| | |
| --- | --- |
| **Entry** | `SharesightTradesSkill` in `skill.py` |
| **Config** | `config.yaml` |
| **Secrets** | `SHARESIGHT_CLIENT_ID` / `SHARESIGHT_CLIENT_SECRET` |

## Worksheet mapping

- `trade/transaction_date` -> column `H` (`Pay Date`)
- `trade/paid_on` -> column `H` (`Pay Date`)
- `trade/price` and `trade/capital_return_value` -> column `L` (`ROC $`)
- `trade/exchange_rate` -> column `S` (`Exchange Rate`)
- `trade/comments` -> `"{ROC%}% {Pay Date short} ROC. Gross Amt ${Gross Amt}"`
  - Example: `94.69% 23-Jan ROC. Gross Amt $571.47`

All trades are sent as `transaction_type: CAPITAL_RETURN`.

## Runtime dry-run override

You can set `dry_run` in config and still override it at runtime:

```bash
PYTHONPATH=/path/to/OpenClaw \
python -c "from openclaw.skills.sharesight_trades.skill import SharesightTradesSkill; print(SharesightTradesSkill(config_path='openclaw/skills/sharesight_trades/config.yaml').run(dry_run=True))"
```

## CLI example

```bash
cd /path/to/OpenClaw
source .venv/bin/activate

export SHARESIGHT_CLIENT_ID="..."
export SHARESIGHT_CLIENT_SECRET="..."
export OPENCLAW_SHARESIGHT_SYNC_EXCEL_PATH="/absolute/path/to/workbook.xlsx"

PYTHONPATH=/path/to/OpenClaw \
python -c "from openclaw.skills.sharesight_trades.skill import SharesightTradesSkill; print(SharesightTradesSkill(config_path='openclaw/skills/sharesight_trades/config.yaml').run())"
```
