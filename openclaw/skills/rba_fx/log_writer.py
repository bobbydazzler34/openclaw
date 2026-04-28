"""Obsidian markdown run log writer for the RBA FX skill."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ObsidianRunLogWriter:
    """Write Obsidian markdown run logs for `RbaFxSkill` executions."""

    logs_dir: Path
    operator: str
    environment: str
    obsidian_user: str
    config_path: str = "skills/rba_fx/config.yaml"
    csv_url: str = "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"
    worksheet_name: str = "FXRates"
    env_var_name: str = "OPENCLAW_RBA_FX_EXCEL_PATH"

    ALLOWED_USERS = {"aashd", "bobbyd"}

    def write_log(
        self,
        result: dict[str, Any],
        *,
        excel_path_resolved: str,
        env_override_used: bool,
        command_used: str = "",
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        notes: list[str] | None = None,
        next_actions: list[str] | None = None,
        stack_trace: str = "",
    ) -> Path:
        """Render and write a run log markdown file to disk."""
        if self.obsidian_user not in self.ALLOWED_USERS:
            msg = f"obsidian_user must be one of {sorted(self.ALLOWED_USERS)}"
            raise ValueError(msg)

        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        run_id = f"{now_utc:%Y-%m-%dT%H-%M-%SZ}_rba_fx_{self.obsidian_user}"
        host = socket.gethostname()

        status = str(result.get("status", "unknown"))
        rows_downloaded = int(result.get("rows_downloaded", 0) or 0)
        rows_appended = int(result.get("rows_appended", 0) or 0)
        latest_sheet_date = str(result.get("latest_sheet_date", "") or "")
        workbook_path = str(result.get("workbook_path", "") or "")
        worksheet_name = str(result.get("worksheet_name", self.worksheet_name) or self.worksheet_name)

        warnings = warnings or []
        errors = errors or []
        notes = notes or []
        next_actions = next_actions or []

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.logs_dir / f"{run_id}.md"

        runtime_json = {
            "status": status,
            "rows_downloaded": rows_downloaded,
            "rows_appended": rows_appended,
            "latest_sheet_date": latest_sheet_date,
            "workbook_path": workbook_path,
            "worksheet_name": worksheet_name,
        }

        markdown = f"""---
type: skill-run-log
skill_id: rba_fx
run_id: "{run_id}"
timestamp_local: "{now_local:%Y-%m-%d %H:%M:%S}"
timestamp_utc: "{now_utc:%Y-%m-%dT%H:%M:%SZ}"
environment: "{self.environment}"
host: "{host}"
operator: "{self.operator}"
status: "{status}"
rows_appended: {rows_appended}
tags:
  - openclaw
  - rba-fx
  - skill-run
  - fxrates
inputs:
  config_path: "{self.config_path}"
  csv_url: "{self.csv_url}"
  excel_path_resolved: "{excel_path_resolved}"
  worksheet_name: "{worksheet_name}"
  env_override_used: {str(env_override_used).lower()}
  env_var_name: "{self.env_var_name}"
outputs:
  rows_downloaded: {rows_downloaded}
  rows_appended: {rows_appended}
  latest_sheet_date_before: "{latest_sheet_date}"
  workbook_path: "{workbook_path}"
  worksheet_name: "{worksheet_name}"
guardrails:
  workbook_closed_before_run: true
  worksheet_exists_confirmed: true
  append_only_newer_dates: true
  format_copy_expected: true
warnings: {json.dumps(warnings)}
errors: {json.dumps(errors)}
---

# RBA FX Run Log - {now_local:%Y-%m-%d %H:%M:%S}

## Summary
- **Status:** {status}
- **Rows Appended:** {rows_appended}
- **Workbook:** {workbook_path}
- **Worksheet:** `{worksheet_name}`
- **Environment:** {self.environment}

## Command Used
```bash
{command_used}
```

## Runtime Result (Raw)
```json
{json.dumps(runtime_json, indent=2)}
```

## Validation Checklist
- [ ] `FXRates` worksheet updated
- [ ] Only newer dates were appended
- [ ] New rows inherited prior row formatting
- [ ] Linked worksheets still compute/display correctly
- [ ] OneDrive sync completed

## Warnings / Errors
### Warnings
{self._to_bullets(warnings)}

### Errors
{self._to_bullets(errors)}

### Stack Trace (if any)
```text
{stack_trace}
```

## Notes
{self._to_bullets(notes)}

## Next Actions
{self._to_bullets(next_actions)}
"""
        out_path.write_text(markdown, encoding="utf-8")
        return out_path

    @staticmethod
    def _to_bullets(items: list[str]) -> str:
        """Convert list entries to markdown bullet lines."""
        if not items:
            return "- None"
        return "\n".join(f"- {item}" for item in items)
