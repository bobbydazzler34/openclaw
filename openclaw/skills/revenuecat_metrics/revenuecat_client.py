# Dashlane secrets (stored as Dashlane Secret items; resolve via `dcli read` when the
# env var in `/etc/openclaw/secrets.env` holds a `dl://` reference, or set plain values
# for local dev):
#   REVENUECAT_API_KEY    -> dl://RevenueCat API Key/password
#                            (secret key sk_... or OAuth access token atk_... with
#                            charts_metrics:overview:read scope)
#   REVENUECAT_PROJECT_ID -> dl://RevenueCat Project ID/note
#
# On the Pi, secrets.env typically contains dl:// references and the unit runs under
# `dcli exec --` so values are resolved at process start. This module also resolves
# dl:// references directly when loading credentials.

"""RevenueCat Charts & Metrics API client."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.revenuecat.com/v2"
OVERVIEW_PATH = "/projects/{project_id}/metrics/overview"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RATE_LIMIT_RETRIES = 6

DASHLANE_DEFAULT_REFS: dict[str, str] = {
    "REVENUECAT_API_KEY": "dl://RevenueCat API Key/password",
    "REVENUECAT_PROJECT_ID": "dl://RevenueCat Project ID/note",
}


class RevenueCatError(Exception):
    """Base error for RevenueCat client failures."""


class RevenueCatInsufficientScopeError(RevenueCatError):
    """Charts & Metrics overview rejected the credential (likely needs OAuth)."""


def utc_today() -> date:
    """Return today's date in UTC (never uses naive local time)."""
    return datetime.now(timezone.utc).date()


def resolve_dashlane_secret(env_name: str, *, default_ref: str | None = None) -> str:
    """Resolve a secret from the environment or Dashlane CLI.

    If the env value starts with ``dl://``, runs ``dcli read``. If unset, tries
    ``default_ref`` (or the built-in default for known keys).
    """
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        raw = (default_ref or DASHLANE_DEFAULT_REFS.get(env_name) or "").strip()
    if not raw:
        msg = (
            f"Secret '{env_name}' is not set and no Dashlane default reference is configured."
        )
        raise RuntimeError(msg)
    return resolve_secret_reference(raw)


def resolve_secret_reference(raw: str) -> str:
    """Return a plaintext secret, resolving ``dl://`` references via Dashlane CLI."""
    value = raw.strip()
    if value.startswith("dl://"):
        return _dcli_read(value)
    return value


def _dcli_read(reference: str) -> str:
    """Read a secret value via Dashlane CLI."""
    try:
        completed = subprocess.run(
            ["dcli", "read", reference],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        msg = (
            f"Dashlane CLI (`dcli`) not found while resolving {reference!r}. "
            f"Install dcli or set {reference} to a plain env value."
        )
        raise RuntimeError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out resolving Dashlane secret {reference!r}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        msg = f"dcli read failed for {reference!r}: {stderr or 'unknown error'}"
        raise RuntimeError(msg)

    value = completed.stdout.strip()
    if not value:
        raise RuntimeError(f"dcli read returned empty value for {reference!r}")
    return value


def parse_retry_after_seconds(response: httpx.Response, *, attempt: int) -> float:
    """Parse Retry-After header or fall back to exponential backoff."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return min(60.0, 2.0**attempt)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Typed overview metrics snapshot."""

    active_trials: int
    active_subscriptions: int
    mrr: float
    revenue_28d: float
    new_customers: int
    active_users: int
    snapshot_at: datetime
    currency: str | None = None


def _metric_value(metrics: list[dict[str, Any]], metric_id: str, *, period: str | None = None) -> float:
    """Return the first matching metric value from the overview payload."""
    for entry in metrics:
        if entry.get("id") != metric_id:
            continue
        if period is not None and entry.get("period") != period:
            continue
        value = entry.get("value")
        if value is None:
            return 0.0
        return float(value)
    return 0.0


def _parse_overview_payload(payload: dict[str, Any], snapshot_at: datetime) -> MetricsSnapshot:
    metrics = payload.get("metrics") or []
    if not isinstance(metrics, list):
        metrics = []

    return MetricsSnapshot(
        active_trials=int(_metric_value(metrics, "active_trials", period="P0D")),
        active_subscriptions=int(_metric_value(metrics, "active_subscriptions", period="P0D")),
        mrr=_metric_value(metrics, "mrr", period="P28D"),
        revenue_28d=_metric_value(metrics, "revenue", period="P28D"),
        new_customers=int(_metric_value(metrics, "new_customers", period="P28D")),
        active_users=int(_metric_value(metrics, "active_users", period="P28D")),
        snapshot_at=snapshot_at,
        currency=payload.get("currency"),
    )


def _check_insufficient_scope(response: httpx.Response) -> None:
    if response.status_code != 403:
        return
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {}

    error_code = body.get("error")
    message = str(body.get("message", ""))
    if error_code == "insufficient_scope" or "insufficient_scope" in message:
        token_hint = (
            "RevenueCat returned 403 insufficient_scope for metrics/overview. "
            "This endpoint requires the charts_metrics:overview:read permission. "
            "If you are using a secret API key (sk_...), create an OAuth access token "
            "(atk_...) with charts_metrics:overview:read in the RevenueCat dashboard "
            "and store it in Dashlane as REVENUECAT_API_KEY instead."
        )
        raise RevenueCatInsufficientScopeError(token_hint)


class RevenueCatClient:
    """HTTP client for RevenueCat overview metrics."""

    def __init__(
        self,
        *,
        project_id: str,
        api_key: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._project_id = project_id.strip()
        self._api_key = api_key.strip()
        self._timeout = timeout
        self._http_client = http_client
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            self._http_client.close()

    def __enter__(self) -> RevenueCatClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _overview_url(self) -> str:
        return f"{BASE_URL}{OVERVIEW_PATH.format(project_id=self._project_id)}"

    def fetch_overview_metrics(self) -> MetricsSnapshot:
        """Fetch overview metrics, respecting 429 Retry-After backoff."""
        snapshot_at = datetime.now(timezone.utc)
        url = self._overview_url()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        client = self._client()

        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            response = client.get(url, headers=headers)
            if response.status_code == 429:
                delay = parse_retry_after_seconds(response, attempt=attempt)
                logger.warning(
                    "RevenueCat rate limit (429); sleeping %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RATE_LIMIT_RETRIES,
                )
                time.sleep(delay)
                continue

            _check_insufficient_scope(response)
            response.raise_for_status()
            payload = response.json()
            return _parse_overview_payload(payload, snapshot_at)

        msg = f"RevenueCat rate limit exceeded after {MAX_RATE_LIMIT_RETRIES} retries"
        raise RevenueCatError(msg)


def fetch_metrics_from_env() -> MetricsSnapshot:
    """Load credentials from env/Dashlane and return overview metrics."""
    api_key = resolve_dashlane_secret("REVENUECAT_API_KEY")
    project_id = resolve_dashlane_secret("REVENUECAT_PROJECT_ID")
    with RevenueCatClient(project_id=project_id, api_key=api_key) as client:
        return client.fetch_overview_metrics()
