"""Obsidian markdown run log writer for the MSTY tracker skill."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ObsidianRunLogWriter:
    """Write Obsidian markdown run logs for `MstyTrackerSkill` executions."""

    logs_dir: Path
    operator: str
    environment: str
    obsidian_user: str
    config_path: str = "skills/msty_tracker/config.yaml"
    source_url: str = "https://yieldmaxetfs.com/our-etfs/msty/"
    env_var_name: str = "OPENCLAW_MSTY_TRACKER_EXCEL_PATH"

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
        run_id = f"{now_utc:%Y-%m-%dT%H-%M-%SZ}_msty_tracker_{self.obsidian_user}"
        host = socket.gethostname()

        status = str(result.get("status", "unknown"))
        rows_found = int(result.get("rows_found", 0) or 0)
        rows_missing = int(result.get("rows_missing", 0) or 0)
        rows_different = int(result.get("rows_different", 0) or 0)
        rows_matching = int(result.get("rows_matching", 0) or 0)
        rows_inserted = int(result.get("rows_inserted", 0) or 0)
        rows_updated = int(result.get("rows_updated", 0) or 0)
        dc_rows_inserted = int(result.get("dc_rows_inserted", 0) or 0)
        dc_rows_updated = int(result.get("dc_rows_updated", 0) or 0)
        workbook_path = str(result.get("workbook_path", "") or "")
        worksheet_name = str(result.get("worksheet_name", "Distributions") or "Distributions")

        warnings = warnings or []
        errors = errors or []
        notes = notes or []
        next_actions = next_actions or []

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.logs_dir / f"{run_id}.md"

        runtime_json = {
            "status": status,
            "rows_found": rows_found,
            "rows_missing": rows_missing,
            "rows_different": rows_different,
            "rows_matching": rows_matching,
            "rows_inserted": rows_inserted,
            "rows_updated": rows_updated,
            "dc_rows_inserted": dc_rows_inserted,
            "dc_rows_updated": dc_rows_updated,
            "workbook_path": workbook_path,
            "worksheet_name": worksheet_name,
        }

        markdown = f"""---
type: skill-run-log
skill_id: msty_tracker
run_id: "{run_id}"
timestamp_local: "{now_local:%Y-%m-%d %H:%M:%S}"
timestamp_utc: "{now_utc:%Y-%m-%dT%H:%M:%SZ}"
environment: "{self.environment}"
host: "{host}"
operator: "{self.operator}"
status: "{status}"
rows_inserted: {rows_inserted}
rows_updated: {rows_updated}
tags:
  - openclaw
  - msty-tracker
  - skill-run
  - distributions
inputs:
  config_path: "{self.config_path}"
  source_url: "{self.source_url}"
  excel_path_resolved: "{excel_path_resolved}"
  worksheet_name: "{worksheet_name}"
  env_override_used: {str(env_override_used).lower()}
  env_var_name: "{self.env_var_name}"
outputs:
  rows_found: {rows_found}
  rows_missing: {rows_missing}
  rows_different: {rows_different}
  rows_matching: {rows_matching}
  rows_inserted: {rows_inserted}
  rows_updated: {rows_updated}
  dc_rows_inserted: {dc_rows_inserted}
  dc_rows_updated: {dc_rows_updated}
  workbook_path: "{workbook_path}"
  worksheet_name: "{worksheet_name}"
warnings: {json.dumps(warnings)}
errors: {json.dumps(errors)}
---

# MSTY Tracker Run Log - {now_local:%Y-%m-%d %H:%M:%S}

## Summary
- **Status:** {status}
- **Rows Found:** {rows_found}
- **Rows Inserted:** {rows_inserted}
- **Rows Updated:** {rows_updated}
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
- [ ] `Distributions` worksheet updated
- [ ] Missing distributions inserted correctly
- [ ] Existing changed rows updated in place
- [ ] DC Pavula ROC% sync reviewed (if enabled)
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
