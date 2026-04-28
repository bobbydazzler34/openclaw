"""Unit tests for the Obsidian run log writer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openclaw.skills.rba_fx.log_writer import ObsidianRunLogWriter


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
                "rows_downloaded": 42,
                "rows_appended": 3,
                "latest_sheet_date": "28-04-2026",
                "workbook_path": "/tmp/Personal CashFlow.xlsx",
                "worksheet_name": "FXRates",
            }

            log_path = writer.write_log(
                result,
                excel_path_resolved="/tmp/Personal CashFlow.xlsx",
                env_override_used=False,
                command_used="python -m openclaw.skills.rba_fx",
                notes=["Smoke run against copied workbook."],
            )

            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("type: skill-run-log", content)
            self.assertIn('skill_id: rba_fx', content)
            self.assertIn('status: "success"', content)
            self.assertIn("rows_appended: 3", content)
            self.assertRegex(content, r'run_id: "\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_rba_fx_obsidian_aashd"')
            self.assertIn('"rows_appended": 3', content)
            self.assertIn("## Runtime Result (Raw)", content)
            self.assertIn("- [ ] `FXRates` worksheet updated", content)

    def test_write_log_rejects_unknown_obsidian_user(self) -> None:
        """Writer should reject users outside the allowed set."""
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ObsidianRunLogWriter(
                logs_dir=Path(temp_dir),
                operator="Chris Cropley",
                environment="macbook",
                obsidian_user="invalid",
            )
            result = {
                "status": "success",
                "rows_downloaded": 1,
                "rows_appended": 1,
                "latest_sheet_date": "01-01-2026",
                "workbook_path": "/tmp/Personal CashFlow.xlsx",
                "worksheet_name": "FXRates",
            }

            with self.assertRaisesRegex(ValueError, "obsidian_user must be one of"):
                writer.write_log(
                    result,
                    excel_path_resolved="/tmp/Personal CashFlow.xlsx",
                    env_override_used=False,
                )


if __name__ == "__main__":
    unittest.main()
