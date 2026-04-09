# msty_tracker

Pull MSTY distribution rows from the YieldMax website, reconcile with your **Distributions** worksheet, and write inserts/updates. Optionally pushes **ROC%** into the **DC Pavula** sheet (`CS FY2526` in code).

| | |
| --- | --- |
| **Entry** | `MstyTrackerSkill` in `skill.py` |
| **Config** | `config.yaml` |
| **Sheets** | `Distributions` (primary); `CS FY2526` (DC Pavula, optional) |

## Configuration highlights

- `excel_path_env_var` — workbook override (default `OPENCLAW_MSTY_TRACKER_EXCEL_PATH`)
- `update_dc_pavula` — run DC Pavula ROC% sync (default `true`)
- `dc_pavula_insert_missing_rows` — insert DC rows when no formula-linked row exists (default `false`)

Add `excel_path` to `config.yaml` if you do not want to use an env var.

## Workbook path (order of precedence)

1. `excel_path=` argument to `MstyTrackerSkill(...)`
2. Environment variable from `excel_path_env_var`
3. `excel_path` in config, else the default in `skill.py`

## `update_dc_pavula`

- **`true`:** After **Distributions** updates, fills **ROC%** on DC Pavula rows that are already linked to the right **Distributions** row (via sheet references in formulas). Creates a **backup** of the workbook before DC writes; see `dc_backup_path` in the result.
- **`false`:** Skips DC Pavula entirely; only **Distributions** may change.

## `dc_pavula_insert_missing_rows`

Used only when `update_dc_pavula` is `true`.

- **`false`:** If there is no DC Pavula row pointing at a given **Distributions** row, ROC% for that distribution is **skipped** and reported in `dc_skipped_no_dc_row` — add the row in Excel or turn inserts on.
- **`true`:** **Inserts** a new DC Pavula row when missing. Risky with Excel tables, array formulas, and merges; check `dc_merge_warnings` (columns H–T).

## CLI example

```bash
cd /path/to/OpenClaw
source .venv/bin/activate

OPENCLAW_MSTY_TRACKER_EXCEL_PATH="/absolute/path/to/workbook.xlsx" \
PYTHONPATH=/path/to/OpenClaw \
python -c "from openclaw.skills.msty_tracker.skill import MstyTrackerSkill; print(MstyTrackerSkill(config_path='openclaw/skills/msty_tracker/config.yaml').run())"
```

If `read_html` fails, try `pip install lxml` in the same venv.

## Dependencies

`pandas`, `requests`, `openpyxl`, `PyYAML`
