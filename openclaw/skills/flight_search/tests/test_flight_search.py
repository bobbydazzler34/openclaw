"""Unit tests for flight_search."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openclaw.skills.flight_search.discord_notifier import (
    build_calendar_embed,
    build_multi_city_calendar_embed,
    build_route_embed,
)
from openclaw.skills.flight_search.flight_search import (
    _date_window,
    _resolve_date,
    _resolve_legs,
    _shift_leg_dates,
)
from openclaw.skills.flight_search.quota import (
    SEARCHAPI_MONTHLY_QUOTA,
    SEARCHAPI_WARNING_THRESHOLD,
    SERPAPI_MONTHLY_QUOTA,
    SERPAPI_WARNING_THRESHOLD,
    check_and_increment,
    format_quota_warning,
)
from openclaw.skills.flight_search.searchapi_calendar_client import (
    CalendarEntry,
    format_calendar_line,
    parse_calendar_results,
    pick_cheapest_date,
)
from openclaw.skills.flight_search.serpapi_client import (
    FlightOption,
    FlightSearchResults,
    MultiCityLeg,
    build_multi_city_json,
    format_flight_block,
    format_flight_line,
    format_route_sequence,
    parse_flight_results,
)

SAMPLE_PAYLOAD = {
    "search_metadata": {
        "google_flights_url": "https://www.google.com/travel/flights?hl=en",
    },
    "price_insights": {
        "lowest_price": 999,
        "price_level": "high",
        "typical_price_range": [1500, 1900],
    },
    "best_flights": [
        {
            "flights": [
                {
                    "airline": "Qantas",
                    "flight_number": "QF 93",
                    "travel_class": "Economy",
                    "departure_airport": {"id": "MEL", "time": "2026-07-16 15:10"},
                    "arrival_airport": {"id": "LAX", "time": "2026-07-16 12:45"},
                },
                {
                    "airline": "United",
                    "flight_number": "UA 837",
                    "departure_airport": {"id": "LAX", "time": "2026-07-16 14:30"},
                    "arrival_airport": {"id": "SFO", "time": "2026-07-16 15:59"},
                },
            ],
            "layovers": [{"id": "LAX", "duration": 45}],
            "carbon_emissions": {
                "this_flight": 1106000,
                "difference_percent": 17,
            },
            "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/multi.png",
            "total_duration": 1120,
            "price": 1899,
        },
        {
            "flights": [{"airline": "Jetstar"}],
            "total_duration": 780,
            "price": 1299,
        },
    ],
    "other_flights": [
        {
            "flights": [{"airline": "Virgin Australia"}],
            "total_duration": 800,
            "price": 1599,
        },
        {
            "flights": [{"airline": "Singapore Airlines"}],
            "total_duration": 900,
            "price": 999,
        },
    ],
}

SAMPLE_CALENDAR_PAYLOAD = {
    "calendar": [
        {"departure": "2026-07-14", "price": 912},
        {"departure": "2026-07-16", "price": 899, "is_lowest_price": True},
        {"departure": "2026-07-18", "price": 945},
        {"departure": "2026-07-20", "has_no_flights": True},
    ]
}


class TestParseFlightResults(unittest.TestCase):
    def test_merges_sorts_and_limits_top_n(self) -> None:
        results = parse_flight_results(SAMPLE_PAYLOAD, currency="AUD", top_n=3)
        prices = [option.price for option in results.options]
        self.assertEqual(prices, [999.0, 1299.0, 1599.0])
        self.assertEqual(len(results.options), 3)

    def test_extracts_metadata(self) -> None:
        results = parse_flight_results(SAMPLE_PAYLOAD, currency="AUD", top_n=3)
        self.assertEqual(
            results.google_flights_url,
            "https://www.google.com/travel/flights?hl=en",
        )
        self.assertIsNotNone(results.price_footer)
        assert results.price_footer is not None
        self.assertIn("Typical", results.price_footer)
        self.assertIn("Open in Google Flights", results.price_footer)

    def test_parses_rich_flight_fields(self) -> None:
        results = parse_flight_results(SAMPLE_PAYLOAD, currency="AUD", top_n=5)
        rich = next(option for option in results.options if option.price == 1899.0)
        self.assertEqual(rich.departure_airport, "MEL")
        self.assertEqual(rich.arrival_airport, "SFO")
        self.assertEqual(rich.departure_time, "15:10")
        self.assertEqual(rich.flight_numbers, ("QF 93", "UA 837"))
        self.assertEqual(rich.layover_labels, ("LAX 45m",))
        self.assertEqual(rich.carbon_kg, 1106)
        self.assertEqual(rich.carbon_diff_percent, 17)


class TestParseCalendarResults(unittest.TestCase):
    def test_sorts_and_skips_no_flights(self) -> None:
        results = parse_calendar_results(SAMPLE_CALENDAR_PAYLOAD, top_n=10)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].departure, "2026-07-16")
        self.assertEqual(results[0].price, 899.0)

    def test_pick_cheapest_date(self) -> None:
        results = parse_calendar_results(SAMPLE_CALENDAR_PAYLOAD, top_n=10)
        self.assertEqual(pick_cheapest_date(results), ("2026-07-16", None))


class TestFlightFormatting(unittest.TestCase):
    def test_format_flight_line(self) -> None:
        option = FlightOption(
            price=1234.0,
            currency="AUD",
            airlines=("Qantas", "United"),
            stops=1,
            total_duration_minutes=1120,
        )
        line = format_flight_line(option)
        self.assertIn("$1,234 AUD", line)
        self.assertIn("Qantas + United", line)

    def test_format_flight_block(self) -> None:
        option = FlightOption(
            price=1899.0,
            currency="AUD",
            airlines=("Qantas", "United"),
            stops=1,
            total_duration_minutes=1120,
            departure_airport="MEL",
            arrival_airport="SFO",
            departure_time="15:10",
            arrival_time="15:59",
            flight_numbers=("QF 93", "UA 837"),
            layover_labels=("LAX 45m",),
            travel_class="Economy",
            carbon_kg=1106,
            carbon_diff_percent=17,
        )
        block = format_flight_block(option, 1)
        self.assertIn("#1", block)
        self.assertIn("MEL 15:10 → 15:59 SFO", block)
        self.assertIn("QF 93 · UA 837", block)
        self.assertIn("Via LAX 45m", block)
        self.assertIn("1,106 kg CO₂ (+17% vs typical)", block)

    def test_format_calendar_line(self) -> None:
        entry = CalendarEntry(departure="2026-07-16", price=899.0, is_lowest_price=True)
        line = format_calendar_line(entry, currency="AUD")
        self.assertIn("2026-07-16", line)
        self.assertIn("$899 AUD", line)
        self.assertIn("★", line)


class TestResolveDate(unittest.TestCase):
    @patch("openclaw.skills.flight_search.flight_search.date")
    def test_days_out_rule(self, mock_date: unittest.mock.MagicMock) -> None:
        mock_date.today.return_value = date(2026, 7, 2)
        route = {"name": "test", "outbound_date_rule": {"type": "days_out", "days": 14}}
        self.assertEqual(_resolve_date(route, prefix="outbound"), "2026-07-16")

    def test_date_window(self) -> None:
        start, end = _date_window("2026-07-16", 14)
        self.assertEqual(start, "2026-07-09")
        self.assertEqual(end, "2026-07-23")


class TestQuota(unittest.TestCase):
    @patch("openclaw.skills.flight_search.quota._current_month")
    def test_serpapi_warning_at_threshold(self, mock_month: unittest.mock.MagicMock) -> None:
        mock_month.return_value = "2026-07"
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "quota_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "month": "2026-07",
                        "serpapi": {"search_count": 199, "warning_sent": False},
                        "searchapi": {"search_count": 0, "warning_sent": False},
                    }
                ),
                encoding="utf-8",
            )

            result = check_and_increment("serpapi", state_path=state_path)

            self.assertTrue(result.allowed)
            self.assertTrue(result.should_warn)
            self.assertEqual(result.search_count, 200)

    @patch("openclaw.skills.flight_search.quota._current_month")
    def test_searchapi_exhausted_blocks_search(self, mock_month: unittest.mock.MagicMock) -> None:
        mock_month.return_value = "2026-07"
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "quota_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "month": "2026-07",
                        "serpapi": {"search_count": 0, "warning_sent": False},
                        "searchapi": {
                            "search_count": SEARCHAPI_MONTHLY_QUOTA,
                            "warning_sent": True,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = check_and_increment("searchapi", state_path=state_path)

        self.assertFalse(result.allowed)

    def test_format_quota_warning(self) -> None:
        from openclaw.skills.flight_search.quota import QuotaCheckResult

        message = format_quota_warning(
            QuotaCheckResult(
                provider="searchapi",
                allowed=True,
                should_warn=True,
                search_count=SEARCHAPI_WARNING_THRESHOLD,
                month="2026-07",
            )
        )
        self.assertIn("SearchAPI", message)
        self.assertIn(f"{SEARCHAPI_WARNING_THRESHOLD}/{SEARCHAPI_MONTHLY_QUOTA}", message)

    @patch("openclaw.skills.flight_search.quota._current_month")
    def test_migrates_legacy_state(self, mock_month: unittest.mock.MagicMock) -> None:
        mock_month.return_value = "2026-07"
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "quota_state.json"
            state_path.write_text(
                json.dumps({"month": "2026-07", "search_count": 5, "warning_sent": False}),
                encoding="utf-8",
            )

            result = check_and_increment("serpapi", state_path=state_path)

        self.assertTrue(result.allowed)
        self.assertEqual(result.search_count, 6)


class TestMultiCity(unittest.TestCase):
    def test_build_multi_city_json(self) -> None:
        legs = [
            MultiCityLeg("MEL", "LHR", "2026-08-01"),
            MultiCityLeg("LHR", "CDG", "2026-08-08", times="8,18,9,23"),
        ]
        payload = json.loads(build_multi_city_json(legs))
        self.assertEqual(payload[0]["departure_id"], "MEL")
        self.assertEqual(payload[0]["date"], "2026-08-01")
        self.assertEqual(payload[1]["times"], "8,18,9,23")
        self.assertNotIn("times", payload[0])

    def test_format_route_sequence_connected(self) -> None:
        legs = [
            MultiCityLeg("MEL", "LHR", "2026-08-01"),
            MultiCityLeg("LHR", "CDG", "2026-08-08"),
            MultiCityLeg("CDG", "MEL", "2026-08-15"),
        ]
        self.assertEqual(format_route_sequence(legs), "MEL → LHR → CDG → MEL")

    def test_format_route_sequence_open_jaw(self) -> None:
        legs = [
            MultiCityLeg("MEL", "SFO", "2026-08-01"),
            MultiCityLeg("LAX", "MEL", "2026-08-15"),
        ]
        self.assertEqual(format_route_sequence(legs), "MEL → SFO · LAX → MEL")

    @patch("openclaw.skills.flight_search.flight_search.date")
    def test_resolve_legs(self, mock_date: unittest.mock.MagicMock) -> None:
        mock_date.today.return_value = date(2026, 7, 2)
        route = {
            "name": "loop",
            "legs": [
                {"origin": "MEL", "destination": "LHR", "date_rule": {"type": "days_out", "days": 30}},
                {"origin": "LHR", "destination": "CDG", "date_rule": {"type": "days_out", "days": 37}},
            ],
        }
        legs = _resolve_legs(route)
        self.assertIsNotNone(legs)
        assert legs is not None
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0].date, "2026-08-01")
        self.assertEqual(legs[1].date, "2026-08-08")

    def test_shift_leg_dates(self) -> None:
        legs = [
            MultiCityLeg("MEL", "LHR", "2026-08-01"),
            MultiCityLeg("LHR", "CDG", "2026-08-08"),
        ]
        shifted = _shift_leg_dates(legs, 3)
        self.assertEqual(shifted[0].date, "2026-08-04")
        self.assertEqual(shifted[1].date, "2026-08-11")


class TestDiscordEmbed(unittest.TestCase):
    def test_build_calendar_embed(self) -> None:
        embed = build_calendar_embed(
            origin="MEL",
            destination="SFO",
            window_label="2026-07-09 to 2026-07-23",
            calendar_lines=["2026-07-16 · $899 AUD ★"],
        )
        self.assertIn("calendar", embed["title"])
        self.assertIn("$899 AUD", embed["description"])

    def test_build_route_embed_one_way(self) -> None:
        embed = build_route_embed(
            origin="MEL",
            destination="SFO",
            outbound_date="2026-07-16",
            return_date=None,
            trip_type="one_way",
            flight_blocks=["**#1 · $1,234 AUD** · Qantas · nonstop · 14h 20m"],
        )
        self.assertEqual(embed["title"], "MEL → SFO — 2026-07-16")

    def test_build_route_embed_rich(self) -> None:
        embed = build_route_embed(
            origin="MEL",
            destination="SFO",
            outbound_date="2026-07-16",
            return_date=None,
            trip_type="one_way",
            flight_blocks=["**#1 · $1,234 AUD** · Qantas · nonstop · 14h 20m"],
            google_flights_url="https://www.google.com/travel/flights?hl=en",
            footer="Typical $1,500–$1,900 · Open in Google Flights",
            thumbnail_url="https://www.gstatic.com/flights/airline_logos/70px/QF.png",
        )
        self.assertEqual(embed["url"], "https://www.google.com/travel/flights?hl=en")
        self.assertIn("Open in Google Flights", embed["footer"]["text"])
        self.assertEqual(
            embed["thumbnail"]["url"],
            "https://www.gstatic.com/flights/airline_logos/70px/QF.png",
        )

    def test_build_route_embed_multi_city(self) -> None:
        embed = build_route_embed(
            origin="MEL",
            destination="MEL",
            outbound_date="2026-08-01",
            return_date=None,
            trip_type="multi_city",
            flight_blocks=["**#1 · $2,500 AUD** · Qantas + Air France · 2 stops · 32h 10m"],
            route_sequence="MEL → LHR → CDG → MEL",
            leg_dates=["2026-08-01", "2026-08-08", "2026-08-15"],
        )
        self.assertEqual(
            embed["title"],
            "MEL → LHR → CDG → MEL — 2026-08-01 · 2026-08-08 · 2026-08-15",
        )

    def test_build_multi_city_calendar_embed(self) -> None:
        embed = build_multi_city_calendar_embed(
            route_sequence="MEL → LHR → CDG → MEL",
            window_label="2026-07-09 to 2026-07-23",
            calendar_lines=["2026-07-16 · $899 AUD ★"],
        )
        self.assertIn("leg 1 calendar", embed["title"])
        self.assertIn("MEL → LHR → CDG → MEL", embed["title"])


if __name__ == "__main__":
    unittest.main()
