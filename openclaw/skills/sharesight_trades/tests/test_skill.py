"""Unit tests for Sharesight CAPITAL_RETURN trades skill."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from openclaw.skills.sharesight_trades.skill import SharesightTradesSkill, TradeRecord


def create_workbook_fixture(workbook_path: Path) -> None:
    """Create workbook with the CS FY2526 columns needed by this skill."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "CS FY2526"
    worksheet.cell(row=6, column=8).value = "CS distributions"
    worksheet.cell(row=7, column=8).value = "Pay Date"
    worksheet.cell(row=7, column=10).value = "ROC%"
    worksheet.cell(row=7, column=12).value = "ROC $"
    worksheet.cell(row=7, column=14).value = "Gross Amt"
    worksheet.cell(row=7, column=19).value = "Exchange Rate"

    worksheet.cell(row=8, column=8).value = datetime(2026, 1, 23)
    worksheet.cell(row=8, column=10).value = 94.69
    worksheet.cell(row=8, column=12).value = 12.34
    worksheet.cell(row=8, column=14).value = 571.47
    worksheet.cell(row=8, column=19).value = 1.45

    workbook.save(workbook_path)
    workbook.close()


def add_workbook_row(
    workbook_path: Path,
    *,
    row_index: int,
    pay_date: datetime,
    roc_percent: float,
    roc_amount,
    gross_amount: float,
    exchange_rate,
) -> None:
    """Append or overwrite one worksheet row for test scenarios."""
    from openpyxl import load_workbook as _load_workbook

    wb = _load_workbook(workbook_path)
    ws = wb["CS FY2526"]
    ws.cell(row=row_index, column=8).value = pay_date
    ws.cell(row=row_index, column=10).value = roc_percent
    ws.cell(row=row_index, column=12).value = roc_amount
    ws.cell(row=row_index, column=14).value = gross_amount
    ws.cell(row=row_index, column=19).value = exchange_rate
    wb.save(workbook_path)
    wb.close()


class FakeApiClient:
    """Minimal API test double for create/list calls."""

    def __init__(self) -> None:
        self.created_payloads: list[dict[str, object]] = []
        self.existing_trades: list[TradeRecord] = []
        self.list_trades_calls: list[tuple[int, object, object]] = []
        self.closed = False

    def resolve_portfolio_id(self, portfolio_name: str) -> int:
        """Return fixed fake portfolio id."""
        return 1201759

    def create_trade(self, payload: dict[str, object]) -> TradeRecord:
        """Record create payload and return fake created trade."""
        self.created_payloads.append(payload)
        return TradeRecord(
            id=9001,
            company_event_id=3001,
            transaction_date=None,
            transaction_type="CAPITAL_RETURN",
            holding_id=1234,
            unique_identifier="uid-created",
            raw={"id": 9001, "company_event_id": 3001},
        )

    def list_trades(self, portfolio_id: int, *, start_date=None, end_date=None) -> list[TradeRecord]:
        """Return configured existing trades."""
        self.list_trades_calls.append((portfolio_id, start_date, end_date))
        return list(self.existing_trades)

    def close(self) -> None:
        """Mark fake client closed."""
        self.closed = True


class TestSharesightTradesSkill(unittest.TestCase):
    """Coverage for worksheet mapping and dry-run behavior."""

    def test_dry_run_can_be_overridden_at_runtime(self) -> None:
        """Runtime dry_run=True avoids API write calls."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "Personal CashFlow.xlsx"
            create_workbook_fixture(workbook_path)
            api = FakeApiClient()
            skill = SharesightTradesSkill(
                excel_path=workbook_path,
                worksheet_name="CS FY2526",
                portfolio_name="DC Pavula",
                holding_id=1234,
                client_id="client-id",
                client_secret="client-secret",
                dry_run=False,
                api_factory=lambda: api,
            )

            result = skill.run(dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["rows_read"], 1)
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["confirmed_count"], 0)
        self.assertEqual(result["matched_and_skipped_count"], 0)
        self.assertEqual(len(result["dry_run_payloads"]), 1)
        self.assertEqual(len(result["dry_run_new_trades"]), 1)
        self.assertEqual(len(result["dry_run_matches"]), 0)
        payload = result["dry_run_payloads"][0]["create_payload"]["trade"]
        self.assertEqual(payload["transaction_type"], "CAPITAL_RETURN")
        self.assertEqual(payload["transaction_date"], "2026-01-23")
        self.assertEqual(payload["paid_on"], "2026-01-23")
        self.assertEqual(payload["price"], 12.34)
        self.assertEqual(payload["capital_return_value"], 12.34)
        self.assertEqual(payload["exchange_rate"], 1.45)
        self.assertEqual(payload["comments"], "94.69% 23-Jan ROC. Gross Amt $571.47")
        self.assertEqual(payload["state"], "confirmed")
        self.assertEqual(api.created_payloads, [])
        self.assertTrue(api.closed)

    def test_run_creates_confirmed_trade(self) -> None:
        """Non dry-run mode creates confirmed trade in one POST."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "Personal CashFlow.xlsx"
            create_workbook_fixture(workbook_path)
            api = FakeApiClient()
            skill = SharesightTradesSkill(
                excel_path=workbook_path,
                worksheet_name="CS FY2526",
                portfolio_name="DC Pavula",
                holding_id=1234,
                client_id="client-id",
                client_secret="client-secret",
                dry_run=False,
                api_factory=lambda: api,
            )

            result = skill.run()

        self.assertFalse(result["dry_run"])
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["confirmed_count"], 1)
        self.assertEqual(result["matched_and_skipped_count"], 0)
        self.assertEqual(result["created_trade_ids"], [9001])
        self.assertEqual(result["confirmed_trade_ids"], [9001])
        self.assertEqual(len(api.created_payloads), 1)
        self.assertEqual(api.created_payloads[0]["trade"]["state"], "confirmed")

    def test_dry_run_logs_matched_trades_as_skipped(self) -> None:
        """Existing same-date CAPITAL_RETURN trades are skipped in dry run."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "Personal CashFlow.xlsx"
            create_workbook_fixture(workbook_path)
            api = FakeApiClient()
            api.existing_trades = [
                TradeRecord(
                    id=20995465,
                    company_event_id=None,
                    transaction_date=datetime(2026, 1, 23).date(),
                    transaction_type="CAPITAL_RETURN",
                    holding_id=1234,
                    unique_identifier="already-here",
                    raw={},
                ),
            ]
            skill = SharesightTradesSkill(
                excel_path=workbook_path,
                worksheet_name="CS FY2526",
                portfolio_name="DC Pavula",
                holding_id=1234,
                client_id="client-id",
                client_secret="client-secret",
                dry_run=True,
                api_factory=lambda: api,
            )

            result = skill.run()

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["confirmed_count"], 0)
        self.assertEqual(result["matched_and_skipped_count"], 1)
        self.assertEqual(len(result["dry_run_matches"]), 1)
        self.assertEqual(len(result["dry_run_new_trades"]), 0)
        self.assertEqual(api.created_payloads, [])

    def test_run_skips_invalid_rows_with_non_positive_roc_amount(self) -> None:
        """Rows with non-positive ROC amount are skipped before API create."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "Personal CashFlow.xlsx"
            create_workbook_fixture(workbook_path)
            add_workbook_row(
                workbook_path,
                row_index=9,
                pay_date=datetime(2026, 2, 1),
                roc_percent=90.0,
                roc_amount=0,
                gross_amount=100.0,
                exchange_rate=1.2,
            )
            api = FakeApiClient()
            skill = SharesightTradesSkill(
                excel_path=workbook_path,
                worksheet_name="CS FY2526",
                portfolio_name="DC Pavula",
                holding_id=1234,
                client_id="client-id",
                client_secret="client-secret",
                dry_run=False,
                api_factory=lambda: api,
            )

            result = skill.run()

        self.assertEqual(result["invalid_rows_skipped_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(
            result["invalid_rows"][0]["reason"],
            "capital_return_value must be greater than zero",
        )

    def test_run_skips_invalid_rows_with_non_positive_exchange_rate(self) -> None:
        """Rows with non-positive exchange rate are skipped before API create."""
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "Personal CashFlow.xlsx"
            create_workbook_fixture(workbook_path)
            add_workbook_row(
                workbook_path,
                row_index=9,
                pay_date=datetime(2026, 2, 1),
                roc_percent=90.0,
                roc_amount=11.5,
                gross_amount=100.0,
                exchange_rate=0,
            )
            api = FakeApiClient()
            skill = SharesightTradesSkill(
                excel_path=workbook_path,
                worksheet_name="CS FY2526",
                portfolio_name="DC Pavula",
                holding_id=1234,
                client_id="client-id",
                client_secret="client-secret",
                dry_run=False,
                api_factory=lambda: api,
            )

            result = skill.run()

        self.assertEqual(result["invalid_rows_skipped_count"], 1)
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(
            result["invalid_rows"][0]["reason"],
            "exchange_rate must be greater than zero",
        )


if __name__ == "__main__":
    unittest.main()
