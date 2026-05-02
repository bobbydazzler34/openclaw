"""Unit tests for the Sharesight trades Obsidian run log writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.skills.sharesight_trades.log_writer import ObsidianRunLogWriter


class TestObsidianRunLogWriter(unittest.TestCase):
    """Coverage for markdown run log rendering and writing."""

    def test_write_log_creates_markdown_file_with_expected_fields(self) -> None:
        """Writer should emit a run log markdown file with template fields."""
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ObsidianRunLogWriter(
                logs_dir=Path(temp_dir),
                operator="Chris Cropley",
                environment="macbook",
                obsidian_user="aashd",
            )
            result = {
                "status": "success",
                "dry_run": True,
                "api_base_url": "https://api.sharesight.com/api/v2",
                "workbook_path": "/tmp/AashCharlesSchwab.xlsx",
                "worksheet_name": "CS FY2526",
                "portfolio_name": "Test Portfolio",
                "holding_id": 26362284,
                "transaction_type": "CAPITAL_RETURN",
                "rows_read": 5,
                "rows_skipped": 0,
                "invalid_rows_skipped_count": 0,
                "existing_trades_fetched": 3,
                "created_count": 0,
                "updated_count": 0,
                "deleted_count": 0,
                "noop_count": 2,
                "created_trade_ids": [],
                "updated_trade_ids": [],
                "deleted_trade_ids": [],
                "reconcile_add": [{"row": 8, "pay_date": "2026-01-23", "create_payload": {"trade": {}}}],
                "reconcile_update": [],
                "reconcile_delete": [],
                "reconcile_noop": [],
                "skipped_rows": [],
                "invalid_rows": [],
                "dry_run_summary": {},
            }

            log_path = writer.write_log(
                result,
                excel_path_resolved="/tmp/AashCharlesSchwab.xlsx",
                env_override_used=False,
                command_used="python -m openclaw.skills.sharesight_trades",
                notes=["Smoke run against test workbook."],
            )

            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("type: skill-run-log", content)
            self.assertIn("skill_id: sharesight_trades", content)
            self.assertIn('status: "success"', content)
            self.assertIn("created_count: 0", content)
            self.assertRegex(
                content,
                r'run_id: "\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_sharesight_trades_aashd"',
            )
            self.assertIn('"create_payload_omitted": true', content)
            self.assertIn("## Runtime Result (summary)", content)
            self.assertIn("- [ ] Dry-run reviewed before disabling `dry_run`", content)

    def test_write_log_rejects_unknown_obsidian_user(self) -> None:
        """Writer should reject users outside the allowed set."""
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ObsidianRunLogWriter(
                logs_dir=Path(temp_dir),
                operator="Chris Cropley",
                environment="macbook",
                obsidian_user="invalid",
            )
            result = {"status": "success", "dry_run": False}

            with self.assertRaisesRegex(ValueError, "obsidian_user must be one of"):
                writer.write_log(
                    result,
                    excel_path_resolved="/tmp/test.xlsx",
                    env_override_used=False,
                )


if __name__ == "__main__":
    unittest.main()
