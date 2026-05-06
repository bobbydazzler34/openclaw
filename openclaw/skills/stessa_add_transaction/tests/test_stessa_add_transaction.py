"""Unit tests for the Stessa add-transaction skill."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from openclaw.skills.stessa_add_transaction.stessa_add_transaction import (
    ParsedTransaction,
    format_success_message,
    parse_instruction_llm,
    run,
    strip_json_fence,
    to_parsed_transaction,
    validate_parsed,
)


class TestStripJson(unittest.TestCase):
    def test_strip_fence(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        self.assertEqual(strip_json_fence(raw), '{"a": 1}')


class TestValidateParsed(unittest.TestCase):
    def test_missing_amount(self) -> None:
        data = {
            "amount": None,
            "date": "2025-05-01",
            "category": "rental_income",
            "subcategory": "rents",
            "property_alias": "ABC",
        }
        err = validate_parsed(data)
        self.assertIsNotNone(err)
        self.assertIn("amount", err or "")

    def test_invalid_category(self) -> None:
        data = {
            "amount": 100.0,
            "date": "2025-05-01",
            "category": "not_a_key",
            "subcategory": "rents",
            "property_alias": "ABC",
        }
        err = validate_parsed(data)
        self.assertIsNotNone(err)
        self.assertIn("Invalid category", err or "")


class TestFormatSuccess(unittest.TestCase):
    def test_message(self) -> None:
        p = ParsedTransaction(
            amount=1200.0,
            date=__import__("datetime").date(2025, 5, 1),
            category_key="rental_income",
            subcategory_key="rents",
            property_alias="ABC",
            transaction_name="Metropole Properties",
            notes=None,
        )
        s = format_success_message(p)
        self.assertIn("1,200.00", s)
        self.assertIn("Rental Income", s)
        self.assertIn("ABC", s)
        self.assertIn("2025-05-01", s)
        self.assertTrue(s.startswith("✅"))


class TestRunIntegrationMocked(unittest.TestCase):
    def test_run_happy_path_mocked(self) -> None:
        cfg = {
            "stessa_username": "u",
            "stessa_password": "p",
            "gemini_api_key_env": "GOOGLE_API_KEY",
        }
        payload = {
            "amount": 50.0,
            "date": "2025-06-01",
            "category": "utilities",
            "subcategory": "water_sewer",
            "property_alias": "Test",
            "name": "Metropole Properties",
            "notes": None,
        }
        with (
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.load_skill_config",
                return_value=cfg,
            ),
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.api_key_from_env",
                return_value="k",
            ),
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.generate_text",
                return_value=json.dumps(payload),
            ),
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.run_sync_playwright",
                return_value="✅ ok",
            ),
        ):
            out = run("pay water")
            self.assertEqual(out, "✅ ok")


class TestParseInstructionLlm(unittest.TestCase):
    def test_parse_calls_gemini(self) -> None:
        cfg = {"gemini_api_key_env": "GOOGLE_API_KEY"}
        with (
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.api_key_from_env",
                return_value="k",
            ),
            patch(
                "openclaw.skills.stessa_add_transaction.stessa_add_transaction.generate_text",
                return_value='{"amount":1,"date":"2025-01-01","category":"rental_income","subcategory":"rents","property_alias":"X","notes":null}',
            ),
        ):
            d = parse_instruction_llm("hello", cfg)
            self.assertEqual(d["amount"], 1)


class TestToParsedTransaction(unittest.TestCase):
    def test_build(self) -> None:
        data = {
            "amount": 10.5,
            "date": "2025-01-02",
            "category": "taxes",
            "subcategory": "city_state_local",
            "property_alias": " P ",
            "name": "Metropole Properties",
            "notes": "  ",
        }
        p = to_parsed_transaction(data)
        self.assertEqual(p.amount, 10.5)
        self.assertIsNone(p.notes)
        self.assertEqual(p.transaction_name, "Metropole Properties")


if __name__ == "__main__":
    unittest.main()
