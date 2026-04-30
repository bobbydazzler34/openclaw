"""Obsidian markdown run log writer for the Sharesight sync skill."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ObsidianRunLogWriter:
    """Write Obsidian markdown run logs for `SharesightSyncSkill` executions."""

    logs_dir: Path
    operator: str
    environment: str
    obsidian_user: str
    config_path: str = "skills/sharesight_sync/config.yaml"
    env_var_name: str = "OPENCLAW_SHARESIGHT_SYNC_EXCEL_PATH"

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
        run_id = f"{now_utc:%Y-%m-%dT%H-%M-%SZ}_sharesight_sync_{self.obsidian_user}"
        host = socket.gethostname()

        status = str(result.get("status", "unknown"))
        dry_run = bool(result.get("dry_run", False))
        portfolio_id = int(result.get("portfolio_id", 0) or 0)
        portfolio_name = str(result.get("portfolio_name", "") or "")
        api_base_url = str(result.get("api_base_url", "") or "")
        workbook_path = str(result.get("workbook_path", "") or "")
        worksheet_name = str(result.get("worksheet_name", "") or "")
        tax_field_name = str(result.get("tax_field_name", "") or "")
        update_by_id = bool(result.get("update_existing_payouts_by_id", False))
        payouts_start = str(result.get("payouts_start_date", "") or "")
        payouts_end = str(result.get("payouts_end_date", "") or "")
        unconfirmed_found = int(result.get("unconfirmed_payouts_found", 0) or 0)
        matched_updated = int(result.get("matched_and_updated", 0) or 0)

        warnings = warnings or []
        errors = errors or []
        notes = notes or []
        next_actions = next_actions or []

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.logs_dir / f"{run_id}.md"

        runtime_json = {
            "status": status,
            "dry_run": dry_run,
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio_name,
            "api_base_url": api_base_url,
            "workbook_path": workbook_path,
            "worksheet_name": worksheet_name,
            "tax_field_name": tax_field_name,
            "update_existing_payouts_by_id": update_by_id,
            "confirmed_state": result.get("confirmed_state"),
            "unconfirmed_state": result.get("unconfirmed_state"),
            "payouts_start_date": payouts_start or None,
            "payouts_end_date": payouts_end or None,
            "unconfirmed_payouts_found": unconfirmed_found,
            "matched_and_updated": matched_updated,
            "matched_pay_dates": result.get("matched_pay_dates"),
            "unmatched_pay_dates": result.get("unmatched_pay_dates"),
            "skipped_worksheet_rows": result.get("skipped_worksheet_rows"),
            "skipped_api_rows": result.get("skipped_api_rows"),
            "dry_run_payloads": result.get("dry_run_payloads"),
            "differing_income_tax_ids": result.get("differing_income_tax_ids"),
        }

        markdown = f"""---
type: skill-run-log
skill_id: sharesight_sync
run_id: "{run_id}"
timestamp_local: "{now_local:%Y-%m-%d %H:%M:%S}"
timestamp_utc: "{now_utc:%Y-%m-%dT%H:%M:%SZ}"
environment: "{self.environment}"
host: "{host}"
operator: "{self.operator}"
status: "{status}"
dry_run: {str(dry_run).lower()}
matched_and_updated: {matched_updated}
tags:
  - openclaw
  - sharesight-sync
  - skill-run
  - payouts
inputs:
  config_path: "{self.config_path}"
  excel_path_resolved: "{excel_path_resolved}"
  worksheet_name: "{worksheet_name}"
  portfolio_name: "{portfolio_name}"
  env_override_used: {str(env_override_used).lower()}
  env_var_name: "{self.env_var_name}"
outputs:
  portfolio_id: {portfolio_id}
  unconfirmed_payouts_found: {unconfirmed_found}
  matched_and_updated: {matched_updated}
  workbook_path: "{workbook_path}"
  worksheet_name: "{worksheet_name}"
  payouts_window_start: "{payouts_start}"
  payouts_window_end: "{payouts_end}"
warnings: {json.dumps(warnings)}
errors: {json.dumps(errors)}
---

# Sharesight Sync Run Log - {now_local:%Y-%m-%d %H:%M:%S}

## Summary
- **Status:** {status}
- **Dry run:** {dry_run}
- **Portfolio:** {portfolio_name} (id {portfolio_id})
- **Matched / updated:** {matched_updated}
- **Unconfirmed payouts seen:** {unconfirmed_found}
- **Workbook:** {workbook_path}
- **Worksheet:** `{worksheet_name}`
- **API:** {api_base_url}
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
- [ ] Worksheet rows align with Sharesight payout dates
- [ ] Unconfirmed payouts reviewed before confirming (if not dry-run)
- [ ] Skipped rows / API rows investigated if non-empty
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
