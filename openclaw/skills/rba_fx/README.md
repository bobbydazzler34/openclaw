# rba_fx

Sync RBA daily AUD/USD FX rates into Excel: downloads the official CSV, compares it to your **FXRates** sheet, and appends only new dates.

| | |
| --- | --- |
| **Entry** | `RbaFxSkill` in `skill.py` |
| **Config** | `config.yaml` |
| **Default sheet** | `FXRates` |

## Configuration highlights

- `csv_url` — RBA CSV source
- `excel_path` — default workbook if env var unset
- `excel_path_env_var` — overrides path (default `OPENCLAW_RBA_FX_EXCEL_PATH`)
- `worksheet_name` — target worksheet

## Workbook path (order of precedence)

1. `excel_path=` argument to `RbaFxSkill(...)`
2. Environment variable from `excel_path_env_var`
3. `excel_path` in `config.yaml`

`~` in paths is expanded.

## CLI example

From the **repo root** (parent of the `openclaw` package):

```bash
cd /path/to/OpenClaw
source .venv/bin/activate

OPENCLAW_RBA_FX_EXCEL_PATH="/absolute/path/to/workbook.xlsx" \
PYTHONPATH=/path/to/OpenClaw \
python -c "from openclaw.skills.rba_fx.skill import RbaFxSkill; print(RbaFxSkill(config_path='openclaw/skills/rba_fx/config.yaml').run())"
```

## Dependencies

`pandas`, `requests`, `openpyxl`, `PyYAML`
