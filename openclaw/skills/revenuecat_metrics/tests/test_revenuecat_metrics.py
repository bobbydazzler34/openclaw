"""Unit tests for revenuecat_metrics."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import httpx

from openclaw.skills.revenuecat_metrics.revenuecat_client import (
    MetricsSnapshot,
    RevenueCatClient,
    RevenueCatInsufficientScopeError,
    parse_retry_after_seconds,
    utc_today,
)


class TestUtcToday(unittest.TestCase):
    @patch("openclaw.skills.revenuecat_metrics.revenuecat_client.datetime")
    def test_utc_today_uses_utc_now(self, mock_datetime: MagicMock) -> None:
        fixed = datetime(2026, 6, 30, 23, 45, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = fixed

        self.assertEqual(utc_today(), date(2026, 6, 30))
        mock_datetime.now.assert_called_once_with(timezone.utc)


class TestRetryAfterBackoff(unittest.TestCase):
    def test_parse_retry_after_header(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "7"}, request=httpx.Request("GET", "http://test"))
        self.assertEqual(parse_retry_after_seconds(response, attempt=0), 7.0)

    def test_parse_retry_after_exponential_fallback(self) -> None:
        response = httpx.Response(429, request=httpx.Request("GET", "http://test"))
        self.assertEqual(parse_retry_after_seconds(response, attempt=2), 4.0)

    @patch("openclaw.skills.revenuecat_metrics.revenuecat_client.time.sleep")
    def test_fetch_overview_retries_on_429(self, mock_sleep: MagicMock) -> None:
        overview_url = "https://api.revenuecat.com/v2/projects/proj123/metrics/overview"
        payload = {
            "object": "overview_metrics",
            "currency": "USD",
            "metrics": [
                {"id": "active_trials", "period": "P0D", "value": 3},
                {"id": "active_subscriptions", "period": "P0D", "value": 10},
                {"id": "mrr", "period": "P28D", "value": 100},
                {"id": "revenue", "period": "P28D", "value": 250},
                {"id": "new_customers", "period": "P28D", "value": 5},
                {"id": "active_users", "period": "P28D", "value": 20},
            ],
        }

        rate_limited = httpx.Response(
            429,
            headers={"Retry-After": "1"},
            request=httpx.Request("GET", overview_url),
        )
        success = httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", overview_url),
        )

        mock_http = MagicMock(spec=httpx.Client)
        mock_http.get.side_effect = [rate_limited, success]

        with RevenueCatClient(project_id="proj123", api_key="atk_test", http_client=mock_http) as client:
            snapshot = client.fetch_overview_metrics()

        self.assertEqual(snapshot.active_subscriptions, 10)
        self.assertEqual(snapshot.mrr, 100.0)
        mock_sleep.assert_called_once_with(1.0)
        self.assertEqual(mock_http.get.call_count, 2)

    def test_insufficient_scope_raises_clear_error(self) -> None:
        overview_url = "https://api.revenuecat.com/v2/projects/proj123/metrics/overview"
        forbidden = httpx.Response(
            403,
            json={"object": "error", "error": "insufficient_scope", "message": "insufficient_scope"},
            request=httpx.Request("GET", overview_url),
        )
        mock_http = MagicMock(spec=httpx.Client)
        mock_http.get.return_value = forbidden

        with RevenueCatClient(project_id="proj123", api_key="sk_test", http_client=mock_http) as client:
            with self.assertRaises(RevenueCatInsufficientScopeError) as ctx:
                client.fetch_overview_metrics()

        self.assertIn("atk_", str(ctx.exception))
        self.assertIn("charts_metrics:overview:read", str(ctx.exception))


class TestFormatSummary(unittest.TestCase):
    def test_format_daily_summary(self) -> None:
        from openclaw.skills.revenuecat_metrics.obsidian_logger import format_daily_summary

        snapshot = MetricsSnapshot(
            active_trials=2,
            active_subscriptions=15,
            mrr=1200.5,
            revenue_28d=3400.0,
            new_customers=8,
            active_users=40,
            snapshot_at=datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc),
            currency="USD",
        )
        text = format_daily_summary(snapshot)
        self.assertIn("Active subscriptions: 15", text)
        self.assertIn("$1,200.50", text)
        self.assertIn("Revenue (28d): $3,400.00", text)


if __name__ == "__main__":
    unittest.main()
