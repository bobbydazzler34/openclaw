"""Obsidian markdown run log writer for the Sharesight trades skill."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def summarize_result_for_log(result: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable summary without full API request payloads."""
    out: dict[str, Any] = {
        "status": result.get("status"),
        "dry_run": result.get("dry_run"),
        "api_base_url": result.get("api_base_url"),
        "workbook_path": result.get("workbook_path"),
        "worksheet_name": result.get("worksheet_name"),
        "portfolio_name": result.get("portfolio_name"),
        "holding_id": result.get("holding_id"),
        "transaction_type": result.get("transaction_type"),
        "rows_read": result.get("rows_read"),
        "rows_skipped": result.get("rows_skipped"),
        "invalid_rows_skipped_count": result.get("invalid_rows_skipped_count"),
        "existing_trades_fetched": result.get("existing_trades_fetched"),
        "created_count": result.get("created_count"),
        "updated_count": result.get("updated_count"),
        "deleted_count": result.get("deleted_count"),
        "noop_count": result.get("noop_count"),
        "created_trade_ids": result.get("created_trade_ids"),
        "updated_trade_ids": result.get("updated_trade_ids"),
        "deleted_trade_ids": result.get("deleted_trade_ids"),
        "dry_run_summary": result.get("dry_run_summary"),
        "skipped_rows": result.get("skipped_rows"),
        "invalid_rows": result.get("invalid_rows"),
    }

    reconcile_add: list[dict[str, Any]] = []
    for item in result.get("reconcile_add") or []:
        if not isinstance(item, dict):
            continue
        reconcile_add.append(
            {
                "row": item.get("row"),
                "pay_date": item.get("pay_date"),
                "create_payload_omitted": True,
            },
        )
    out["reconcile_add"] = reconcile_add

    reconcile_update: list[dict[str, Any]] = []
    for item in result.get("reconcile_update") or []:
        if not isinstance(item, dict):
            continue
        reconcile_update.append(
            {
                "row": item.get("row"),
                "pay_date": item.get("pay_date"),
                "trade_id": item.get("trade_id"),
                "existing_roc": item.get("existing_roc"),
                "existing_exchange_rate": item.get("existing_exchange_rate"),
                "existing_price": item.get("existing_price"),
                "desired_roc": item.get("desired_roc"),
                "desired_exchange_rate": item.get("desired_exchange_rate"),
                "update_payload_omitted": True,
            },
        )
    out["reconcile_update"] = reconcile_update

    out["reconcile_delete"] = result.get("reconcile_delete")
    out["reconcile_noop"] = result.get("reconcile_noop")

    return out


@dataclass(slots=True)
class ObsidianRunLogWriter:
    """Write Obsidian markdown run logs for `SharesightTradesSkill` executions."""

    logs_dir: Path
    operator: str
    environment: str
    obsidian_user: str
    config_path: str = "skills/sharesight_trades/config.yaml"
    api_base_url: str = "https://api.sharesight.com/api/v2"
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
        run_id = f"{now_utc:%Y-%m-%dT%H-%M-%SZ}_sharesight_trades_{self.obsidian_user}"
        host = socket.gethostname()

        status = str(result.get("status", "unknown"))
        dry_run = bool(result.get("dry_run", False))
        created_count = int(result.get("created_count", 0) or 0)
        updated_count = int(result.get("updated_count", 0) or 0)
        deleted_count = int(result.get("deleted_count", 0) or 0)
        noop_count = int(result.get("noop_count", 0) or 0)
        workbook_path = str(result.get("workbook_path", "") or "")
        worksheet_name = str(result.get("worksheet_name", "") or "")
        portfolio_name = str(result.get("portfolio_name", "") or "")

        warnings = warnings or []
        errors = errors or []
        notes = notes or []
        next_actions = next_actions or []

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.logs_dir / f"{run_id}.md"

        runtime_json = summarize_result_for_log(result)

        markdown = f"""---
type: skill-run-log
skill_id: sharesight_trades
run_id: "{run_id}"
timestamp_local: "{now_local:%Y-%m-%d %H:%M:%S}"
timestamp_utc: "{now_utc:%Y-%m-%dT%H:%M:%SZ}"
environment: "{self.environment}"
host: "{host}"
operator: "{self.operator}"
status: "{status}"
dry_run: {str(dry_run).lower()}
created_count: {created_count}
updated_count: {updated_count}
deleted_count: {deleted_count}
noop_count: {noop_count}
tags:
  - openclaw
  - sharesight-trades
  - skill-run
  - capital-return
inputs:
  config_path: "{self.config_path}"
  api_base_url: "{self.api_base_url}"
  excel_path_resolved: "{excel_path_resolved}"
  env_override_used: {str(env_override_used).lower()}
  env_var_name: "{self.env_var_name}"
outputs:
  workbook_path: "{workbook_path}"
  worksheet_name: "{worksheet_name}"
  portfolio_name: "{portfolio_name}"
  created_count: {created_count}
  updated_count: {updated_count}
  deleted_count: {deleted_count}
  noop_count: {noop_count}
guardrails:
  worksheet_headers_present: true
  holding_id_configured: true
  capital_return_only: true
warnings: {json.dumps(warnings)}
errors: {json.dumps(errors)}
---

# Sharesight Trades Run Log - {now_local:%Y-%m-%d %H:%M:%S}

## Summary
- **Status:** {status}
- **Dry run:** {dry_run}
- **Created:** {created_count} | **Updated:** {updated_count} | **Deleted:** {deleted_count} | **No-op:** {noop_count}
- **Workbook:** {workbook_path}
- **Worksheet:** `{worksheet_name}`
- **Portfolio:** {portfolio_name}
- **Environment:** {self.environment}

## Command Used
```bash
{command_used}
```

## Runtime Result (summary)
```json
{json.dumps(runtime_json, indent=2)}
```

## Validation Checklist
- [ ] Spreadsheet rows match intended CAPITAL_RETURN trades
- [ ] Sharesight portfolio and holding_id are correct for this run
- [ ] Dry-run reviewed before disabling `dry_run`

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
