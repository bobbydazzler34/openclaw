"""Async SearchAPI.io Google Flights Calendar client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from openclaw.secrets import get_secret

logger = logging.getLogger(__name__)

SEARCHAPI_URL = "https://www.searchapi.io/api/v1/search"


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """Normalized calendar price entry."""

    departure: str
    price: float
    return_date: str | None = None
    is_lowest_price: bool = False
    has_no_flights: bool = False


def _format_price(price: float, currency: str) -> str:
    if currency.upper() == "AUD":
        return f"${price:,.0f} AUD"
    if currency.upper() == "USD":
        return f"${price:,.0f} USD"
    return f"{price:,.0f} {currency}"


def format_calendar_line(entry: CalendarEntry, *, currency: str) -> str:
    """Format a calendar entry as a Discord embed description line."""
    price_label = _format_price(entry.price, currency)
    lowest = " ★" if entry.is_lowest_price else ""

    if entry.return_date:
        return f"{entry.departure} → {entry.return_date} · {price_label}{lowest}"

    return f"{entry.departure} · {price_label}{lowest}"


def parse_calendar_results(
    payload: dict[str, Any],
    *,
    top_n: int = 10,
) -> list[CalendarEntry]:
    """Parse calendar[] from SearchAPI, sort by price, return top N."""
    raw = payload.get("calendar") or []
    if not isinstance(raw, list):
        return []

    entries: list[CalendarEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("has_no_flights"):
            continue

        departure = item.get("departure")
        price = item.get("price")
        if not departure or price is None:
            continue

        try:
            price_value = float(price)
        except (TypeError, ValueError):
            logger.warning("Skipping calendar entry with invalid price: %r", price)
            continue

        return_date = item.get("return")
        entries.append(
            CalendarEntry(
                departure=str(departure),
                return_date=str(return_date) if return_date else None,
                price=price_value,
                is_lowest_price=bool(item.get("is_lowest_price")),
                has_no_flights=False,
            )
        )

    entries.sort(key=lambda entry: entry.price)
    return entries[:top_n]


def pick_cheapest_date(entries: list[CalendarEntry]) -> tuple[str, str | None] | None:
    """Return (outbound_date, return_date) for the cheapest calendar entry."""
    if not entries:
        return None
    cheapest = entries[0]
    return cheapest.departure, cheapest.return_date


async def search_calendar(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    outbound_date_start: str,
    outbound_date_end: str,
    return_date: str | None = None,
    return_date_start: str | None = None,
    return_date_end: str | None = None,
    adults: int = 1,
    children: int = 0,
    trip_type: str = "one_way",
    gl: str = "au",
    hl: str = "en",
    currency: str = "AUD",
    top_n: int = 10,
    timeout_seconds: float = 30.0,
) -> list[CalendarEntry]:
    """Search Google Flights calendar via SearchAPI.io."""
    api_key = get_secret("SEARCHAPI_KEY")

    params: dict[str, str | int] = {
        "engine": "google_flights_calendar",
        "api_key": api_key,
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": outbound_date,
        "outbound_date_start": outbound_date_start,
        "outbound_date_end": outbound_date_end,
        "flight_type": trip_type,
        "adults": adults,
        "children": children,
        "gl": gl,
        "hl": hl,
        "currency": currency,
    }

    if trip_type == "round_trip":
        if not return_date or not return_date_start or not return_date_end:
            logger.warning(
                "Round-trip calendar %s→%s missing return date range — skipping",
                origin,
                destination,
            )
            return []
        params["return_date"] = return_date
        params["return_date_start"] = return_date_start
        params["return_date_end"] = return_date_end

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(SEARCHAPI_URL, params=params)
            if response.status_code == 429:
                logger.warning(
                    "SearchAPI rate limit for calendar %s→%s",
                    origin,
                    destination,
                )
                return []

            payload = response.json()
    except httpx.TimeoutException:
        logger.warning(
            "SearchAPI calendar request timed out for %s→%s",
            origin,
            destination,
        )
        return []
    except httpx.HTTPError as exc:
        logger.warning(
            "SearchAPI calendar HTTP error for %s→%s: %s",
            origin,
            destination,
            type(exc).__name__,
        )
        return []
    except ValueError:
        logger.warning(
            "SearchAPI calendar returned malformed JSON for %s→%s",
            origin,
            destination,
        )
        return []

    if not isinstance(payload, dict):
        logger.warning(
            "SearchAPI calendar returned unexpected payload for %s→%s",
            origin,
            destination,
        )
        return []

    error = payload.get("error")
    if error:
        logger.warning(
            "SearchAPI calendar error for %s→%s: %s",
            origin,
            destination,
            error,
        )
        return []

    results = parse_calendar_results(payload, top_n=top_n)
    if not results:
        logger.info(
            "No calendar results for %s→%s (%s to %s)",
            origin,
            destination,
            outbound_date_start,
            outbound_date_end,
        )
    return results
