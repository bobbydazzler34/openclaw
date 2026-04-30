"""Unit tests for the Obsidian run log writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.skills.sharesight_sync.log_writer import ObsidianRunLogWriter


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
                "portfolio_id": 1201759,
                "portfolio_name": "DC Pavula",
                "api_base_url": "https://api.sharesight.com/api/v2",
                "workbook_path": "/tmp/Personal CashFlow.xlsx",
                "worksheet_name": "CS FY2526",
                "tax_field_name": "non_resident_withholding_tax",
                "update_existing_payouts_by_id": False,
                "confirmed_state": "confirmed",
                "unconfirmed_state": "unconfirmed",
                "payouts_start_date": "2026-01-01",
                "payouts_end_date": "2026-03-31",
                "unconfirmed_payouts_found": 2,
                "matched_and_updated": 2,
                "matched_pay_dates": ["05/01/2026"],
                "unmatched_pay_dates": [],
                "skipped_worksheet_rows": [],
                "skipped_api_rows": [],
                "dry_run_payloads": [],
                "differing_income_tax_ids": [],
            }

            log_path = writer.write_log(
                result,
                excel_path_resolved="/tmp/Personal CashFlow.xlsx",
                env_override_used=False,
                command_used="python -c \"from openclaw.skills.sharesight_sync.skill import SharesightSyncSkill\"",
                notes=["Smoke run with fake API."],
            )

            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("type: skill-run-log", content)
            self.assertIn("skill_id: sharesight_sync", content)
            self.assertIn('status: "success"', content)
            self.assertIn("matched_and_updated: 2", content)
            self.assertRegex(
                content,
                r'run_id: "\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_sharesight_sync_aashd"',
            )
            self.assertIn('"matched_and_updated": 2', content)
            self.assertIn("## Runtime Result (Raw)", content)
            self.assertIn("- [ ] Worksheet rows align with Sharesight payout dates", content)

    def test_write_log_rejects_unknown_obsidian_user(self) -> None:
        """Writer should reject users outside the allowed set."""
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ObsidianRunLogWriter(
                logs_dir=Path(temp_dir),
                operator="Chris Cropley",
                environment="macbook",
                obsidian_user="invalid",
            )
            result = {"status": "success", "portfolio_id": 1}

            with self.assertRaisesRegex(ValueError, "obsidian_user must be one of"):
                writer.write_log(
                    result,
                    excel_path_resolved="/tmp/workbook.xlsx",
                    env_override_used=False,
                )


if __name__ == "__main__":
    unittest.main()
