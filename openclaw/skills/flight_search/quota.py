"""Monthly search quota tracking for SerpApi and SearchAPI.io."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Provider = Literal["serpapi", "searchapi"]

SERPAPI_MONTHLY_QUOTA = 250
SERPAPI_WARNING_THRESHOLD = 200

SEARCHAPI_MONTHLY_QUOTA = 100
SEARCHAPI_WARNING_THRESHOLD = 80

QUOTA_LIMITS: dict[Provider, int] = {
    "serpapi": SERPAPI_MONTHLY_QUOTA,
    "searchapi": SEARCHAPI_MONTHLY_QUOTA,
}

WARNING_THRESHOLDS: dict[Provider, int] = {
    "serpapi": SERPAPI_WARNING_THRESHOLD,
    "searchapi": SEARCHAPI_WARNING_THRESHOLD,
}

PROVIDER_LABELS: dict[Provider, str] = {
    "serpapi": "SerpApi",
    "searchapi": "SearchAPI",
}

DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "quota_state.json"


@dataclass(frozen=True, slots=True)
class QuotaCheckResult:
    """Outcome of a quota check before performing a search."""

    provider: Provider
    allowed: bool
    should_warn: bool
    search_count: int
    month: str


def _current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read quota state from %s", path)
        return {}

    return data if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict) -> None:
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    except OSError:
        logger.exception("Failed to write quota state to %s", path)


def _empty_provider_state() -> dict:
    return {"search_count": 0, "warning_sent": False}


def _migrate_legacy_state(raw: dict, month: str) -> dict:
    """Support older quota_state.json with flat search_count fields."""
    if "serpapi" in raw or "searchapi" in raw:
        return raw

    if raw.get("month") != month:
        return {
            "month": month,
            "serpapi": _empty_provider_state(),
            "searchapi": _empty_provider_state(),
        }

    legacy_count = raw.get("search_count", 0)
    try:
        legacy_count = int(legacy_count)
    except (TypeError, ValueError):
        legacy_count = 0

    return {
        "month": month,
        "serpapi": {
            "search_count": max(legacy_count, 0),
            "warning_sent": bool(raw.get("warning_sent", False)),
        },
        "searchapi": _empty_provider_state(),
    }


def _normalize_state(raw: dict) -> dict:
    month = _current_month()
    state = _migrate_legacy_state(raw, month)

    if state.get("month") != month:
        return {
            "month": month,
            "serpapi": _empty_provider_state(),
            "searchapi": _empty_provider_state(),
        }

    normalized: dict = {"month": month}
    for provider in ("serpapi", "searchapi"):
        provider_state = state.get(provider) or {}
        if not isinstance(provider_state, dict):
            provider_state = {}

        search_count = provider_state.get("search_count", 0)
        try:
            search_count = int(search_count)
        except (TypeError, ValueError):
            search_count = 0

        normalized[provider] = {
            "search_count": max(search_count, 0),
            "warning_sent": bool(provider_state.get("warning_sent", False)),
        }

    return normalized


def check_and_increment(
    provider: Provider,
    *,
    state_path: Path | None = None,
) -> QuotaCheckResult:
    """Check provider quota, increment when allowed, and flag one-time warnings."""
    path = state_path or DEFAULT_STATE_PATH
    month = _current_month()
    monthly_quota = QUOTA_LIMITS[provider]
    warning_threshold = WARNING_THRESHOLDS[provider]
    label = PROVIDER_LABELS[provider]

    try:
        state = _normalize_state(_load_state(path))
    except Exception:
        logger.exception("%s quota state unavailable — allowing search", label)
        return QuotaCheckResult(
            provider=provider,
            allowed=True,
            should_warn=False,
            search_count=0,
            month=month,
        )

    provider_state = state[provider]
    search_count = provider_state["search_count"]
    if search_count >= monthly_quota:
        logger.warning(
            "%s monthly quota exhausted (%d/%d) for %s — skipping search",
            label,
            search_count,
            monthly_quota,
            month,
        )
        return QuotaCheckResult(
            provider=provider,
            allowed=False,
            should_warn=False,
            search_count=search_count,
            month=month,
        )

    next_count = search_count + 1
    should_warn = next_count >= warning_threshold and not provider_state["warning_sent"]

    provider_state["search_count"] = next_count
    if should_warn:
        provider_state["warning_sent"] = True
    state[provider] = provider_state

    try:
        _save_state(path, state)
    except Exception:
        logger.exception("Failed to persist %s quota state — allowing search", label)

    return QuotaCheckResult(
        provider=provider,
        allowed=True,
        should_warn=should_warn,
        search_count=next_count,
        month=month,
    )


def format_quota_warning(result: QuotaCheckResult) -> str:
    """Format the one-time monthly quota warning message."""
    monthly_quota = QUOTA_LIMITS[result.provider]
    label = PROVIDER_LABELS[result.provider]
    return (
        f"{label} flight search quota at {result.search_count}/{monthly_quota} "
        f"for {result.month}"
    )
