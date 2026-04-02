"""Unit tests for the RBA FX skill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from openclaw.skills.rba_fx.skill import RbaFxSkill


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_rba.csv"
TEST_WORKBOOK_PATH = Path("/tmp/Personal CashFlow.xlsx")


@dataclass
class DummyRowDimension:
    """Minimal row dimension stand-in for unit tests."""

    height: float | None = 18.0
    hidden: bool = False


class DummyStyle:
    """Simple copyable style object for worksheet tests."""

    def __init__(self, name: str) -> None:
        self.name = name


class DummyCell:
    """Minimal cell stand-in for unit tests."""

    def __init__(self, value: object = None, *, has_style: bool = False) -> None:
        self.value = value
        self.has_style = has_style
        self.font = DummyStyle("font")
        self.fill = DummyStyle("fill")
        self.border = DummyStyle("border")
        self.alignment = DummyStyle("alignment")
        self.protection = DummyStyle("protection")
        self.number_format = "General"


class DummyWorksheet:
    """Minimal worksheet stand-in for append and style-copy tests."""

    def __init__(self) -> None:
        self.max_column = 3
        self.max_row = 3
        self.rows: dict[tuple[int, int], DummyCell] = {}
        self.row_dimensions = {
            1: DummyRowDimension(),
            2: DummyRowDimension(height=20.0, hidden=False),
            3: DummyRowDimension(height=22.0, hidden=False),
        }

        self._set_row(1, ["Date", "AUDUSD", "USDAUD"], styled=False)
        self._set_row(2, [date(2024, 1, 1), 0.6612, 1 / 0.6612], styled=True)
        self._set_row(3, [date(2024, 1, 2), 0.6645, 1 / 0.6645], styled=True)

        for column_index in range(1, self.max_column + 1):
            self.rows[(3, column_index)].number_format = "0.0000"

    def _set_row(self, row_index: int, values: list[object], *, styled: bool) -> None:
        for column_index, value in enumerate(values, start=1):
            self.rows[(row_index, column_index)] = DummyCell(value, has_style=styled)

    def append(self, values: list[object]) -> None:
        self.max_row += 1
        self.row_dimensions[self.max_row] = DummyRowDimension()
        self._set_row(self.max_row, values, styled=False)

    def cell(self, row: int, column: int) -> DummyCell:
        key = (row, column)
        if key not in self.rows:
            self.rows[key] = DummyCell()
        return self.rows[key]


class DummyWorkbook:
    """Minimal workbook stand-in for unit tests."""

    def __init__(self, worksheet: DummyWorksheet) -> None:
        self.worksheet = worksheet
        self.saved_path: Path | None = None
        self.closed = False

    def __getitem__(self, name: str) -> DummyWorksheet:
        if name != "FXRates":
            raise KeyError(name)
        return self.worksheet

    def save(self, path: Path) -> None:
        self.saved_path = path

    def close(self) -> None:
        self.closed = True


def load_sample_csv() -> str:
    """Return the sample RBA CSV fixture contents."""
    return FIXTURE_PATH.read_text(encoding="utf-8")


class TestRbaFxSkill(unittest.TestCase):
    """Unit coverage for the RBA FX skill."""

    def test_run_appends_only_missing_rows_and_writes_updated_sheet(self) -> None:
        """The skill appends only newer rows and writes the merged sheet."""
        existing_sheet = pd.DataFrame(
            {
                "Date": ["01-01-2024", "02-01-2024"],
                "AUDUSD": [0.6612, 0.6645],
                "USDAUD": [1 / 0.6612, 1 / 0.6645],
            },
        )
        response = Mock()
        response.text = load_sample_csv()
        response.raise_for_status.return_value = None
        worksheet = DummyWorksheet()
        workbook = DummyWorkbook(worksheet)

        with (
            patch("openclaw.skills.rba_fx.skill.requests.get", return_value=response) as mock_get,
            patch("openclaw.skills.rba_fx.skill.pd.read_excel", return_value=existing_sheet) as mock_read_excel,
            patch("openclaw.skills.rba_fx.skill.load_workbook", return_value=workbook) as mock_load_workbook,
        ):
            skill = RbaFxSkill(
                excel_path=TEST_WORKBOOK_PATH,
                worksheet_name="FXRates",
            )
            result = skill.run()

        mock_get.assert_called_once()
        mock_read_excel.assert_called_once_with(TEST_WORKBOOK_PATH, sheet_name="FXRates")
        mock_load_workbook.assert_called_once_with(TEST_WORKBOOK_PATH)
        self.assertEqual(result["latest_sheet_date"], "02-01-2024")
        self.assertEqual(result["rows_appended"], 2)
        self.assertEqual(workbook.saved_path, TEST_WORKBOOK_PATH)
        self.assertTrue(workbook.closed)

        self.assertEqual(worksheet.cell(4, 1).value, date(2024, 1, 3))
        self.assertAlmostEqual(float(worksheet.cell(4, 2).value), 0.6681)
        self.assertAlmostEqual(float(worksheet.cell(4, 3).value), 1 / 0.6681)
        self.assertEqual(worksheet.cell(5, 1).value, date(2024, 1, 4))
        self.assertAlmostEqual(float(worksheet.cell(5, 3).value), 1 / 0.6714)
        self.assertEqual(worksheet.cell(4, 1).number_format, "0.0000")
        self.assertEqual(worksheet.row_dimensions[4].height, worksheet.row_dimensions[3].height)

    def test_run_raises_clear_error_for_missing_worksheet(self) -> None:
        """A missing worksheet raises a clear error."""
        response = Mock()
        response.text = load_sample_csv()
        response.raise_for_status.return_value = None

        with (
            patch("openclaw.skills.rba_fx.skill.requests.get", return_value=response),
            patch(
                "openclaw.skills.rba_fx.skill.pd.read_excel",
                side_effect=ValueError("Worksheet named 'FXRates' not found"),
            ),
        ):
            skill = RbaFxSkill(excel_path=TEST_WORKBOOK_PATH)

            with self.assertRaisesRegex(ValueError, "Worksheet 'FXRates' was not found"):
                skill.run()

    def test_run_raises_runtime_error_for_network_failure(self) -> None:
        """Network failures are surfaced as runtime errors."""
        with patch(
            "openclaw.skills.rba_fx.skill.requests.get",
            side_effect=requests.RequestException("boom"),
        ):
            skill = RbaFxSkill(excel_path=TEST_WORKBOOK_PATH)

            with self.assertRaisesRegex(RuntimeError, "Unable to download RBA FX data."):
                skill.run()

    def test_parse_rba_csv_skips_malformed_rows(self) -> None:
        """Malformed rows are skipped during CSV parsing."""
        csv_text = "\n".join(
            [
                "Date,AUD/USD",
                "01-Jan-2024,0.6612",
                "bad-date,0.6645",
                "03-Jan-2024,not-a-number",
                "04-Jan-2024,0.6714",
            ],
        )
        skill = RbaFxSkill(excel_path=TEST_WORKBOOK_PATH)

        parsed = skill._parse_rba_csv(csv_text)

        self.assertListEqual(list(parsed["Date"]), ["01-01-2024", "04-01-2024"])
        self.assertAlmostEqual(float(parsed.loc[0, "AUDUSD"]), 0.6612)
        self.assertAlmostEqual(float(parsed.loc[1, "AUDUSD"]), 0.6714)
        self.assertAlmostEqual(float(parsed.loc[0, "USDAUD"]), 1 / 0.6612)
        self.assertAlmostEqual(float(parsed.loc[1, "USDAUD"]), 1 / 0.6714)

    def test_parse_rba_csv_handles_rba_metadata_format(self) -> None:
        """The parser handles the live RBA CSV metadata rows."""
        csv_text = "\n".join(
            [
                "\ufeffF11.1  EXCHANGE RATES",
                "Title,A$1=USD,Trade-weighted Index May 1970 = 100",
                "Description,AUD/USD Exchange Rate; see notes for further detail.,Australian Dollar Trade-weighted Index",
                "Frequency,Daily,Daily",
                "Type,Indicative,Indicative",
                "Units,USD,Index",
                "",
                "Source,WM/Reuters,RBA",
                "Publication date,31-Mar-2026,31-Mar-2026",
                "Series ID,FXRUSD,FXRTWI",
                "03-Jan-2023,0.6828,61.40",
                "04-Jan-2023,0.6809,61.50",
            ],
        )
        skill = RbaFxSkill(excel_path=TEST_WORKBOOK_PATH)

        parsed = skill._parse_rba_csv(csv_text)

        self.assertListEqual(list(parsed["Date"]), ["03-01-2023", "04-01-2023"])
        self.assertAlmostEqual(float(parsed.loc[0, "AUDUSD"]), 0.6828)
        self.assertAlmostEqual(float(parsed.loc[1, "USDAUD"]), 1 / 0.6809)


if __name__ == "__main__":
    unittest.main()
