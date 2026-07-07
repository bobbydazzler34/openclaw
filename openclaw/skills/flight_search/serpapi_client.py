"""Async SerpApi Google Flights client and response parsing."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from openclaw.secrets import get_secret

logger = logging.getLogger(__name__)

SERPAPI_SEARCH_URL = "https://serpapi.com/search"


@dataclass(frozen=True, slots=True)
class MultiCityLeg:
    """Single leg of a multi-city itinerary for SerpApi."""

    departure_id: str
    arrival_id: str
    date: str
    times: str | None = None


@dataclass(frozen=True, slots=True)
class FlightOption:
    """Normalized flight result for display."""

    price: float
    currency: str
    airlines: tuple[str, ...]
    stops: int
    total_duration_minutes: int
    departure_airport: str | None = None
    arrival_airport: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    flight_numbers: tuple[str, ...] = ()
    layover_labels: tuple[str, ...] = ()
    travel_class: str | None = None
    carbon_kg: int | None = None
    carbon_diff_percent: int | None = None
    airline_logo: str | None = None

    @property
    def duration_label(self) -> str:
        hours, minutes = divmod(self.total_duration_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours}h"
        return f"{minutes}m"

    @property
    def stops_label(self) -> str:
        if self.stops == 0:
            return "nonstop"
        if self.stops == 1:
            return "1 stop"
        return f"{self.stops} stops"

    @property
    def airlines_label(self) -> str:
        return " + ".join(self.airlines) if self.airlines else "Unknown"


@dataclass(frozen=True, slots=True)
class FlightSearchResults:
    """Parsed SerpApi flight search response."""

    options: list[FlightOption]
    google_flights_url: str | None = None
    price_footer: str | None = None


EMPTY_FLIGHT_SEARCH_RESULTS = FlightSearchResults([])


def _format_price(price: float, currency: str) -> str:
    if currency.upper() == "AUD":
        return f"${price:,.0f} AUD"
    if currency.upper() == "USD":
        return f"${price:,.0f} USD"
    return f"{price:,.0f} {currency}"


def format_route_sequence(legs: list[MultiCityLeg]) -> str:
    """Build route title for Discord/logging.

    Connected legs use a compact chain (MEL → LHR → CDG → MEL).
    Open-jaw legs list each segment (MEL → SFO · LAX → MEL).
    """
    if not legs:
        return ""

    connected = all(
        legs[i].arrival_id == legs[i + 1].departure_id for i in range(len(legs) - 1)
    )
    if connected:
        airports = [legs[0].departure_id]
        airports.extend(leg.arrival_id for leg in legs)
        return " → ".join(airports)

    return " · ".join(f"{leg.departure_id} → {leg.arrival_id}" for leg in legs)


def build_multi_city_json(legs: list[MultiCityLeg]) -> str:
    """Serialize legs for SerpApi multi_city_json parameter."""
    payload: list[dict[str, str]] = []
    for leg in legs:
        entry: dict[str, str] = {
            "departure_id": leg.departure_id,
            "arrival_id": leg.arrival_id,
            "date": leg.date,
        }
        if leg.times:
            entry["times"] = leg.times
        payload.append(entry)
    return json.dumps(payload, separators=(",", ":"))


def _format_clock_time(raw_time: str | None) -> str | None:
    """Extract HH:MM from SerpApi datetime strings like '2023-10-03 15:10'."""
    if not raw_time or not isinstance(raw_time, str):
        return None
    parts = raw_time.split()
    if len(parts) >= 2 and ":" in parts[1]:
        return parts[1][:5]
    return None


def _airport_field(airport: Any, field: str) -> str | None:
    if not isinstance(airport, dict):
        return None
    value = airport.get(field)
    return str(value) if value else None


def _format_layover_label(layover: dict[str, Any]) -> str | None:
    airport_id = layover.get("id")
    duration = layover.get("duration")
    if not airport_id:
        return None
    try:
        minutes = int(duration)
    except (TypeError, ValueError):
        return str(airport_id)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        duration_label = f"{hours}h{mins:02d}m"
    elif hours:
        duration_label = f"{hours}h"
    else:
        duration_label = f"{mins}m"
    return f"{airport_id} {duration_label}"


def _parse_price_footer(payload: dict[str, Any], *, currency: str) -> str | None:
    insights = payload.get("price_insights")
    if not isinstance(insights, dict):
        return None

    parts: list[str] = []
    typical_range = insights.get("typical_price_range")
    if isinstance(typical_range, list) and len(typical_range) >= 2:
        try:
            low = _format_price(float(typical_range[0]), currency)
            high = _format_price(float(typical_range[1]), currency)
            parts.append(f"Typical {low}–{high}")
        except (TypeError, ValueError):
            pass

    lowest_price = insights.get("lowest_price")
    price_level = insights.get("price_level")
    if lowest_price is not None:
        try:
            lowest_label = _format_price(float(lowest_price), currency)
            if isinstance(price_level, str) and price_level:
                parts.append(f"Lowest {lowest_label} ({price_level})")
            else:
                parts.append(f"Lowest {lowest_label}")
        except (TypeError, ValueError):
            pass

    if not parts:
        return None
    parts.append("Open in Google Flights")
    return " · ".join(parts)


def format_flight_line(option: FlightOption) -> str:
    """Format a single flight option as a one-line summary."""
    return (
        f"{_format_price(option.price, option.currency)} · "
        f"{option.airlines_label} · {option.stops_label} · {option.duration_label}"
    )


def format_flight_block(option: FlightOption, rank: int) -> str:
    """Format a single flight option as a multi-line Discord embed block."""
    header = (
        f"**#{rank} · {_format_price(option.price, option.currency)}** · "
        f"{option.airlines_label} · {option.stops_label} · {option.duration_label}"
    )
    lines = [header]

    if option.departure_airport and option.arrival_airport:
        time_bits = []
        if option.departure_time:
            time_bits.append(option.departure_time)
        time_bits.append("→")
        if option.arrival_time:
            time_bits.append(option.arrival_time)
        route_line = f"{option.departure_airport} {' '.join(time_bits)} {option.arrival_airport}"
        if option.travel_class:
            route_line = f"{route_line} · {option.travel_class}"
        lines.append(route_line)

    if option.flight_numbers:
        lines.append(" · ".join(option.flight_numbers))

    if option.layover_labels:
        lines.append(f"Via {' · '.join(option.layover_labels)}")

    if option.carbon_kg is not None:
        carbon_line = f"{option.carbon_kg:,} kg CO₂"
        if option.carbon_diff_percent is not None:
            sign = "+" if option.carbon_diff_percent >= 0 else ""
            carbon_line = f"{carbon_line} ({sign}{option.carbon_diff_percent}% vs typical)"
        lines.append(carbon_line)

    return "\n".join(lines)


def _parse_flight_option(item: dict[str, Any], *, currency: str) -> FlightOption | None:
    price = item.get("price")
    if price is None:
        logger.warning("Skipping flight entry with missing price")
        return None

    try:
        price_value = float(price)
    except (TypeError, ValueError):
        logger.warning("Skipping flight entry with invalid price: %r", price)
        return None

    segments = item.get("flights") or []
    if not isinstance(segments, list):
        logger.warning("Skipping flight entry with invalid flights array")
        return None

    airlines: list[str] = []
    flight_numbers: list[str] = []
    travel_class: str | None = None
    departure_airport: str | None = None
    arrival_airport: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None

    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        airline = segment.get("airline")
        if isinstance(airline, str) and airline and airline not in airlines:
            airlines.append(airline)

        flight_number = segment.get("flight_number")
        if isinstance(flight_number, str) and flight_number:
            flight_numbers.append(flight_number)

        if index == 0:
            travel_class_value = segment.get("travel_class")
            if isinstance(travel_class_value, str) and travel_class_value:
                travel_class = travel_class_value
            departure_airport = _airport_field(segment.get("departure_airport"), "id")
            departure_time = _format_clock_time(
                _airport_field(segment.get("departure_airport"), "time")
            )

        if index == len(segments) - 1:
            arrival_airport = _airport_field(segment.get("arrival_airport"), "id")
            arrival_time = _format_clock_time(
                _airport_field(segment.get("arrival_airport"), "time")
            )

    layover_labels: list[str] = []
    layovers = item.get("layovers") or []
    if isinstance(layovers, list):
        for layover in layovers:
            if isinstance(layover, dict):
                label = _format_layover_label(layover)
                if label:
                    layover_labels.append(label)

    carbon_kg: int | None = None
    carbon_diff_percent: int | None = None
    carbon = item.get("carbon_emissions")
    if isinstance(carbon, dict):
        this_flight = carbon.get("this_flight")
        diff = carbon.get("difference_percent")
        try:
            if this_flight is not None:
                carbon_kg = round(int(this_flight) / 1000)
        except (TypeError, ValueError):
            carbon_kg = None
        try:
            if diff is not None:
                carbon_diff_percent = int(diff)
        except (TypeError, ValueError):
            carbon_diff_percent = None

    airline_logo = item.get("airline_logo")
    logo = str(airline_logo) if isinstance(airline_logo, str) and airline_logo else None

    total_duration = item.get("total_duration")
    try:
        duration_minutes = int(total_duration) if total_duration is not None else 0
    except (TypeError, ValueError):
        logger.warning("Skipping flight entry with invalid total_duration: %r", total_duration)
        duration_minutes = 0

    stops = max(len(segments) - 1, 0)
    return FlightOption(
        price=price_value,
        currency=currency,
        airlines=tuple(airlines),
        stops=stops,
        total_duration_minutes=duration_minutes,
        departure_airport=departure_airport,
        arrival_airport=arrival_airport,
        departure_time=departure_time,
        arrival_time=arrival_time,
        flight_numbers=tuple(flight_numbers),
        layover_labels=tuple(layover_labels),
        travel_class=travel_class,
        carbon_kg=carbon_kg,
        carbon_diff_percent=carbon_diff_percent,
        airline_logo=logo,
    )


def parse_flight_results(
    payload: dict[str, Any],
    *,
    currency: str,
    top_n: int = 5,
) -> FlightSearchResults:
    """Merge best_flights and other_flights, sort by price, return top N."""
    best = payload.get("best_flights") or []
    other = payload.get("other_flights") or []
    if not isinstance(best, list):
        best = []
    if not isinstance(other, list):
        other = []

    options: list[FlightOption] = []
    for item in [*best, *other]:
        if not isinstance(item, dict):
            continue
        parsed = _parse_flight_option(item, currency=currency)
        if parsed is not None:
            options.append(parsed)

    options.sort(key=lambda option: option.price)
    top_options = options[:top_n]

    metadata = payload.get("search_metadata")
    google_flights_url: str | None = None
    if isinstance(metadata, dict):
        url = metadata.get("google_flights_url")
        if isinstance(url, str) and url:
            google_flights_url = url

    return FlightSearchResults(
        options=top_options,
        google_flights_url=google_flights_url,
        price_footer=_parse_price_footer(payload, currency=currency),
    )


async def _fetch_serpapi_payload(
    params: dict[str, str | int],
    *,
    route_label: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """Execute a SerpApi request and return the JSON payload or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(SERPAPI_SEARCH_URL, params=params)
            if response.status_code == 429:
                logger.warning("SerpApi rate limit for %s", route_label)
                return None

            payload = response.json()
    except httpx.TimeoutException:
        logger.warning("SerpApi request timed out for %s", route_label)
        return None
    except httpx.HTTPError as exc:
        logger.warning("SerpApi HTTP error for %s: %s", route_label, type(exc).__name__)
        return None
    except ValueError:
        logger.warning("SerpApi returned malformed JSON for %s", route_label)
        return None

    if not isinstance(payload, dict):
        logger.warning("SerpApi returned unexpected payload type for %s", route_label)
        return None

    error = payload.get("error")
    if error:
        logger.warning("SerpApi error for %s: %s", route_label, error)
        return None

    return payload


async def search_flights(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    trip_type: str = "one_way",
    gl: str = "au",
    hl: str = "en",
    currency: str = "AUD",
    deep_search: bool = False,
    top_n: int = 5,
    timeout_seconds: float = 30.0,
) -> FlightSearchResults:
    """Search Google Flights via SerpApi and return the cheapest top N options."""
    api_key = get_secret("SERPAPI_KEY")
    flight_type = "1" if trip_type == "round_trip" else "2"

    params: dict[str, str | int] = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": outbound_date,
        "type": flight_type,
        "adults": adults,
        "children": children,
        "gl": gl,
        "hl": hl,
        "currency": currency,
        "deep_search": "true" if deep_search else "false",
    }
    if trip_type == "round_trip":
        if not return_date:
            logger.warning(
                "Round-trip search %s→%s missing return_date — skipping",
                origin,
                destination,
            )
            return EMPTY_FLIGHT_SEARCH_RESULTS
        params["return_date"] = return_date

    route_label = f"{origin}→{destination} on {outbound_date}"
    payload = await _fetch_serpapi_payload(
        params,
        route_label=route_label,
        timeout_seconds=timeout_seconds,
    )
    if payload is None:
        return EMPTY_FLIGHT_SEARCH_RESULTS

    results = parse_flight_results(payload, currency=currency, top_n=top_n)
    if not results.options:
        logger.info(
            "No flight results for %s→%s on %s",
            origin,
            destination,
            outbound_date,
        )
    return results


async def search_multi_city_flights(
    *,
    legs: list[MultiCityLeg],
    adults: int = 1,
    children: int = 0,
    gl: str = "au",
    hl: str = "en",
    currency: str = "AUD",
    deep_search: bool = False,
    top_n: int = 5,
    timeout_seconds: float = 30.0,
) -> FlightSearchResults:
    """Search multi-city Google Flights via SerpApi (type=3) and return top N options."""
    if len(legs) < 2:
        logger.warning("Multi-city search requires at least 2 legs — skipping")
        return EMPTY_FLIGHT_SEARCH_RESULTS

    api_key = get_secret("SERPAPI_KEY")
    route_label = format_route_sequence(legs)
    params: dict[str, str | int] = {
        "engine": "google_flights",
        "api_key": api_key,
        "type": "3",
        "multi_city_json": build_multi_city_json(legs),
        "adults": adults,
        "children": children,
        "gl": gl,
        "hl": hl,
        "currency": currency,
        "deep_search": "true" if deep_search else "false",
    }

    payload = await _fetch_serpapi_payload(
        params,
        route_label=route_label,
        timeout_seconds=timeout_seconds,
    )
    if payload is None:
        return EMPTY_FLIGHT_SEARCH_RESULTS

    results = parse_flight_results(payload, currency=currency, top_n=top_n)
    if not results.options:
        logger.info("No multi-city flight results for %s", route_label)
    return results
