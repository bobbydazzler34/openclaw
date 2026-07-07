"""Flight search orchestrator — SearchAPI calendar + SerpApi details to Discord."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from openclaw.secrets import get_secret
from openclaw.skills.flight_search.discord_notifier import (
    build_calendar_embed,
    build_multi_city_calendar_embed,
    build_route_embed,
    send_embed_safe,
    send_message_safe,
)
from openclaw.skills.flight_search.quota import (
    check_and_increment,
    format_quota_warning,
)
from openclaw.skills.flight_search.searchapi_calendar_client import (
    CalendarEntry,
    format_calendar_line,
    pick_cheapest_date,
    search_calendar,
)
from openclaw.skills.flight_search.serpapi_client import (
    MultiCityLeg,
    FlightSearchResults,
    build_multi_city_json,
    format_flight_block,
    format_route_sequence,
    search_flights,
    search_multi_city_flights,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _resolve_date(route: dict[str, Any], *, prefix: str) -> str | None:
    """Resolve a fixed date or days_out rule for outbound/return."""
    fixed_key = f"{prefix}_date"
    rule_key = f"{prefix}_date_rule"

    fixed = route.get(fixed_key)
    if fixed:
        return str(fixed)

    rule = route.get(rule_key)
    if not isinstance(rule, dict):
        logger.error("Route %r missing %s or %s", route.get("name"), fixed_key, rule_key)
        return None

    rule_type = rule.get("type")
    if rule_type != "days_out":
        logger.error("Route %r has unsupported date rule type: %r", route.get("name"), rule_type)
        return None

    days = rule.get("days")
    try:
        days_out = int(days)
    except (TypeError, ValueError):
        logger.error("Route %r has invalid days_out value: %r", route.get("name"), days)
        return None

    return (date.today() + timedelta(days=days_out)).isoformat()


def _resolve_leg_date(leg: dict[str, Any], *, route_name: str, leg_index: int) -> str | None:
    """Resolve a fixed date or days_out rule for a multi-city leg."""
    fixed = leg.get("date")
    if fixed:
        return str(fixed)

    rule = leg.get("date_rule")
    if not isinstance(rule, dict):
        logger.error(
            "Route %r leg %d missing date or date_rule",
            route_name,
            leg_index,
        )
        return None

    rule_type = rule.get("type")
    if rule_type != "days_out":
        logger.error(
            "Route %r leg %d has unsupported date rule type: %r",
            route_name,
            leg_index,
            rule_type,
        )
        return None

    days = rule.get("days")
    try:
        days_out = int(days)
    except (TypeError, ValueError):
        logger.error(
            "Route %r leg %d has invalid days_out value: %r",
            route_name,
            leg_index,
            days,
        )
        return None

    return (date.today() + timedelta(days=days_out)).isoformat()


def _resolve_legs(route: dict[str, Any]) -> list[MultiCityLeg] | None:
    """Validate and resolve multi-city legs from route config."""
    name = route.get("name", "unnamed_route")
    raw_legs = route.get("legs")
    if not isinstance(raw_legs, list) or len(raw_legs) < 2:
        logger.error("Route %r requires legs list with at least 2 entries", name)
        return None

    legs: list[MultiCityLeg] = []
    for index, raw_leg in enumerate(raw_legs, start=1):
        if not isinstance(raw_leg, dict):
            logger.error("Route %r leg %d is not a mapping — skipping route", name, index)
            return None

        origin = raw_leg.get("origin")
        destination = raw_leg.get("destination")
        if not origin or not destination:
            logger.error("Route %r leg %d missing origin or destination", name, index)
            return None

        leg_date = _resolve_leg_date(raw_leg, route_name=name, leg_index=index)
        if not leg_date:
            return None

        times = raw_leg.get("times")
        legs.append(
            MultiCityLeg(
                departure_id=str(origin),
                arrival_id=str(destination),
                date=leg_date,
                times=str(times) if times else None,
            )
        )

    return legs


def _shift_leg_dates(legs: list[MultiCityLeg], delta_days: int) -> list[MultiCityLeg]:
    """Shift all leg dates by delta_days, preserving relative spacing."""
    if delta_days == 0:
        return legs

    shifted: list[MultiCityLeg] = []
    for leg in legs:
        shifted_date = date.fromisoformat(leg.date) + timedelta(days=delta_days)
        shifted.append(
            MultiCityLeg(
                departure_id=leg.departure_id,
                arrival_id=leg.arrival_id,
                date=shifted_date.isoformat(),
                times=leg.times,
            )
        )
    return shifted


def _date_window(anchor: str, window_days: int) -> tuple[str, str]:
    """Return inclusive start/end ISO dates centered on anchor."""
    anchor_date = date.fromisoformat(anchor)
    half = max(window_days // 2, 0)
    start = anchor_date - timedelta(days=half)
    end = anchor_date + timedelta(days=half)
    return start.isoformat(), end.isoformat()


def _calendar_settings(config: dict[str, Any], route: dict[str, Any]) -> tuple[int, int]:
    calendar_cfg = config.get("calendar") or {}
    if not isinstance(calendar_cfg, dict):
        calendar_cfg = {}

    window_days = int(route.get("calendar_window_days", calendar_cfg.get("window_days", 14)))
    top_dates = int(route.get("calendar_top_dates", calendar_cfg.get("top_dates", 10)))
    return window_days, top_dates


def _search_mode(config: dict[str, Any], route: dict[str, Any]) -> str:
    return str(route.get("search_mode", config.get("search_mode", "calendar_then_flights")))


async def _maybe_warn(
    result: Any,
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
    timeout_seconds: float,
) -> None:
    if result.should_warn and not dry_run:
        await send_message_safe(
            format_quota_warning(result),
            bot_token=bot_token,
            channel_id=channel_id,
            timeout=timeout_seconds,
        )


async def _post_flights_embed(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None,
    trip_type: str,
    results: FlightSearchResults,
    bot_token: str,
    channel_id: str,
    timeout_seconds: float,
    route_sequence: str | None = None,
    leg_dates: list[str] | None = None,
) -> None:
    flight_blocks = [
        format_flight_block(option, rank)
        for rank, option in enumerate(results.options, start=1)
    ]
    thumbnail_url = results.options[0].airline_logo if results.options else None
    embed = build_route_embed(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        trip_type=trip_type,
        flight_blocks=flight_blocks,
        route_sequence=route_sequence,
        leg_dates=leg_dates,
        google_flights_url=results.google_flights_url,
        footer=results.price_footer,
        thumbnail_url=thumbnail_url,
    )
    await send_embed_safe(
        embed,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout=timeout_seconds,
    )


async def _run_serpapi_search(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None,
    trip_type: str,
    adults: int,
    children: int,
    gl: str,
    hl: str,
    currency: str,
    deep_search: bool,
    top_n: int,
    timeout_seconds: float,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> bool:
    """Run SerpApi detail search when quota allows. Returns True if search ran."""
    if dry_run:
        logger.info(
            "Dry run — would run SerpApi %s→%s outbound=%s return=%s",
            origin,
            destination,
            outbound_date,
            return_date or "n/a",
        )
        return True

    quota_result = check_and_increment("serpapi")
    await _maybe_warn(
        quota_result,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
    )
    if not quota_result.allowed:
        return False

    results = await search_flights(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        adults=adults,
        children=children,
        trip_type=trip_type,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
    )
    await _post_flights_embed(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        trip_type=trip_type,
        results=results,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
    )
    return True


async def _run_serpapi_multi_city_search(
    *,
    legs: list[MultiCityLeg],
    adults: int,
    children: int,
    gl: str,
    hl: str,
    currency: str,
    deep_search: bool,
    top_n: int,
    timeout_seconds: float,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> bool:
    """Run SerpApi multi-city search when quota allows. Returns True if search ran."""
    route_sequence = format_route_sequence(legs)
    leg_dates = [leg.date for leg in legs]

    if dry_run:
        logger.info(
            "Dry run — would run SerpApi multi-city %s legs=%s",
            route_sequence,
            build_multi_city_json(legs),
        )
        return True

    quota_result = check_and_increment("serpapi")
    await _maybe_warn(
        quota_result,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
    )
    if not quota_result.allowed:
        return False

    results = await search_multi_city_flights(
        legs=legs,
        adults=adults,
        children=children,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
    )
    await _post_flights_embed(
        origin=legs[0].departure_id,
        destination=legs[-1].arrival_id,
        outbound_date=legs[0].date,
        return_date=None,
        trip_type="multi_city",
        results=results,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
        route_sequence=route_sequence,
        leg_dates=leg_dates,
    )
    return True


async def _process_route_calendar_then_flights(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    name = route.get("name", "unnamed_route")
    origin = str(route.get("origin", ""))
    destination = str(route.get("destination", ""))
    trip_type = str(route.get("trip_type", "one_way"))

    outbound_anchor = _resolve_date(route, prefix="outbound")
    if not outbound_anchor:
        return

    return_anchor: str | None = None
    if trip_type == "round_trip":
        return_anchor = _resolve_date(route, prefix="return")
        if not return_anchor:
            return

    timeout_seconds = float(config.get("http_timeout_seconds", 30))
    top_n = int(route.get("top_n", config.get("default_top_n", 5)))
    adults = int(route.get("adults", 1))
    children = int(route.get("children", 0))
    gl = str(config.get("gl", "au"))
    hl = str(config.get("hl", "en"))
    currency = str(config.get("currency", "AUD"))
    deep_search = bool(config.get("deep_search", False))
    window_days, top_dates = _calendar_settings(config, route)

    outbound_start, outbound_end = _date_window(outbound_anchor, window_days)
    return_start: str | None = None
    return_end: str | None = None
    if trip_type == "round_trip" and return_anchor:
        return_start, return_end = _date_window(return_anchor, window_days)

    window_label = f"{outbound_start} to {outbound_end}"
    if trip_type == "round_trip" and return_start and return_end:
        window_label = f"{outbound_start}–{outbound_end} / return {return_start}–{return_end}"

    calendar_entries: list[CalendarEntry] = []
    detail_outbound = outbound_anchor
    detail_return = return_anchor

    if dry_run:
        logger.info(
            "Dry run — would run SearchAPI calendar for %s→%s window %s (top %d dates)",
            origin,
            destination,
            window_label,
            top_dates,
        )
        logger.info(
            "Dry run — would run SerpApi detail for %s→%s on cheapest calendar date",
            origin,
            destination,
        )
        return

    calendar_quota = check_and_increment("searchapi")
    await _maybe_warn(
        calendar_quota,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
    )

    if calendar_quota.allowed:
        logger.info(
            "Calendar search for route %r: %s→%s window %s",
            name,
            origin,
            destination,
            window_label,
        )
        try:
            calendar_entries = await search_calendar(
                origin=origin,
                destination=destination,
                outbound_date=outbound_anchor,
                outbound_date_start=outbound_start,
                outbound_date_end=outbound_end,
                return_date=return_anchor,
                return_date_start=return_start,
                return_date_end=return_end,
                adults=adults,
                children=children,
                trip_type=trip_type,
                gl=gl,
                hl=hl,
                currency=currency,
                top_n=top_dates,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.exception("Calendar search failed for route %r", name)
            calendar_entries = []

        if calendar_entries:
            calendar_lines = [
                format_calendar_line(entry, currency=currency) for entry in calendar_entries
            ]
            calendar_embed = build_calendar_embed(
                origin=origin,
                destination=destination,
                window_label=window_label,
                calendar_lines=calendar_lines,
            )
            await send_embed_safe(
                calendar_embed,
                bot_token=bot_token,
                channel_id=channel_id,
                timeout=timeout_seconds,
            )

            cheapest = pick_cheapest_date(calendar_entries)
            if cheapest:
                detail_outbound, picked_return = cheapest
                if picked_return:
                    detail_return = picked_return
    else:
        logger.warning(
            "SearchAPI quota exhausted — skipping calendar for route %r, using anchor date",
            name,
        )

    if not calendar_entries and not calendar_quota.allowed:
        logger.info(
            "Falling back to SerpApi-only search on anchor date for route %r",
            name,
        )

    await _run_serpapi_search(
        origin=origin,
        destination=destination,
        outbound_date=detail_outbound,
        return_date=detail_return,
        trip_type=trip_type,
        adults=adults,
        children=children,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


async def _process_route_flights_only(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    name = route.get("name", "unnamed_route")
    origin = str(route.get("origin", ""))
    destination = str(route.get("destination", ""))
    trip_type = str(route.get("trip_type", "one_way"))

    outbound_date = _resolve_date(route, prefix="outbound")
    if not outbound_date:
        return

    return_date: str | None = None
    if trip_type == "round_trip":
        return_date = _resolve_date(route, prefix="return")
        if not return_date:
            return

    timeout_seconds = float(config.get("http_timeout_seconds", 30))
    top_n = int(route.get("top_n", config.get("default_top_n", 5)))
    adults = int(route.get("adults", 1))
    children = int(route.get("children", 0))
    gl = str(config.get("gl", "au"))
    hl = str(config.get("hl", "en"))
    currency = str(config.get("currency", "AUD"))
    deep_search = bool(config.get("deep_search", False))

    if dry_run:
        logger.info(
            "Dry run — would run SerpApi-only %s→%s outbound=%s return=%s",
            origin,
            destination,
            outbound_date,
            return_date or "n/a",
        )

    logger.info(
        "SerpApi-only route %r: %s→%s outbound=%s return=%s",
        name,
        origin,
        destination,
        outbound_date,
        return_date or "n/a",
    )

    await _run_serpapi_search(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        return_date=return_date,
        trip_type=trip_type,
        adults=adults,
        children=children,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


async def _process_route_multi_city_flights_only(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    name = route.get("name", "unnamed_route")
    legs = _resolve_legs(route)
    if not legs:
        return

    route_sequence = format_route_sequence(legs)
    timeout_seconds = float(config.get("http_timeout_seconds", 30))
    top_n = int(route.get("top_n", config.get("default_top_n", 5)))
    adults = int(route.get("adults", 1))
    children = int(route.get("children", 0))
    gl = str(config.get("gl", "au"))
    hl = str(config.get("hl", "en"))
    currency = str(config.get("currency", "AUD"))
    deep_search = bool(config.get("deep_search", False))

    if dry_run:
        logger.info(
            "Dry run — would run SerpApi multi-city %s legs=%s",
            route_sequence,
            build_multi_city_json(legs),
        )

    logger.info(
        "SerpApi multi-city route %r: %s dates=%s",
        name,
        route_sequence,
        " · ".join(leg.date for leg in legs),
    )

    await _run_serpapi_multi_city_search(
        legs=legs,
        adults=adults,
        children=children,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


async def _process_route_multi_city_calendar_then_flights(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    name = route.get("name", "unnamed_route")
    anchor_legs = _resolve_legs(route)
    if not anchor_legs:
        return

    route_sequence = format_route_sequence(anchor_legs)
    leg1 = anchor_legs[0]
    anchor_leg1_date = leg1.date

    timeout_seconds = float(config.get("http_timeout_seconds", 30))
    top_n = int(route.get("top_n", config.get("default_top_n", 5)))
    adults = int(route.get("adults", 1))
    children = int(route.get("children", 0))
    gl = str(config.get("gl", "au"))
    hl = str(config.get("hl", "en"))
    currency = str(config.get("currency", "AUD"))
    deep_search = bool(config.get("deep_search", False))
    window_days, top_dates = _calendar_settings(config, route)

    outbound_start, outbound_end = _date_window(anchor_leg1_date, window_days)
    window_label = f"{outbound_start} to {outbound_end}"

    detail_legs = anchor_legs
    calendar_entries: list[CalendarEntry] = []

    if dry_run:
        logger.info(
            "Dry run — would run SearchAPI leg-1 calendar for %s window %s (top %d dates)",
            route_sequence,
            window_label,
            top_dates,
        )
        logger.info(
            "Dry run — would run SerpApi multi-city %s on shifted leg dates",
            route_sequence,
        )
        return

    calendar_quota = check_and_increment("searchapi")
    await _maybe_warn(
        calendar_quota,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
        timeout_seconds=timeout_seconds,
    )

    if calendar_quota.allowed:
        logger.info(
            "Leg-1 calendar search for route %r: %s→%s window %s",
            name,
            leg1.departure_id,
            leg1.arrival_id,
            window_label,
        )
        try:
            calendar_entries = await search_calendar(
                origin=leg1.departure_id,
                destination=leg1.arrival_id,
                outbound_date=anchor_leg1_date,
                outbound_date_start=outbound_start,
                outbound_date_end=outbound_end,
                return_date=None,
                return_date_start=None,
                return_date_end=None,
                adults=adults,
                children=children,
                trip_type="one_way",
                gl=gl,
                hl=hl,
                currency=currency,
                top_n=top_dates,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.exception("Leg-1 calendar search failed for route %r", name)
            calendar_entries = []

        if calendar_entries:
            calendar_lines = [
                format_calendar_line(entry, currency=currency) for entry in calendar_entries
            ]
            calendar_embed = build_multi_city_calendar_embed(
                route_sequence=route_sequence,
                window_label=window_label,
                calendar_lines=calendar_lines,
            )
            await send_embed_safe(
                calendar_embed,
                bot_token=bot_token,
                channel_id=channel_id,
                timeout=timeout_seconds,
            )

            cheapest = pick_cheapest_date(calendar_entries)
            if cheapest:
                cheapest_leg1_date, _ = cheapest
                delta_days = (
                    date.fromisoformat(cheapest_leg1_date)
                    - date.fromisoformat(anchor_leg1_date)
                ).days
                detail_legs = _shift_leg_dates(anchor_legs, delta_days)
    else:
        logger.warning(
            "SearchAPI quota exhausted — skipping leg-1 calendar for route %r, using anchor dates",
            name,
        )

    if not calendar_entries and not calendar_quota.allowed:
        logger.info(
            "Falling back to SerpApi-only multi-city search on anchor dates for route %r",
            name,
        )

    await _run_serpapi_multi_city_search(
        legs=detail_legs,
        adults=adults,
        children=children,
        gl=gl,
        hl=hl,
        currency=currency,
        deep_search=deep_search,
        top_n=top_n,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


async def _process_route_multi_city(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    mode = _search_mode(config, route)
    if mode == "flights_only":
        await _process_route_multi_city_flights_only(
            route,
            config,
            dry_run=dry_run,
            bot_token=bot_token,
            channel_id=channel_id,
        )
        return

    if mode != "calendar_then_flights":
        logger.error(
            "Route %r has unsupported search_mode %r — skipping",
            route.get("name", "unnamed_route"),
            mode,
        )
        return

    await _process_route_multi_city_calendar_then_flights(
        route,
        config,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


async def _process_route(
    route: dict[str, Any],
    config: dict[str, Any],
    *,
    dry_run: bool,
    bot_token: str,
    channel_id: str,
) -> None:
    name = route.get("name", "unnamed_route")
    trip_type = str(route.get("trip_type", "one_way"))

    if trip_type == "multi_city":
        await _process_route_multi_city(
            route,
            config,
            dry_run=dry_run,
            bot_token=bot_token,
            channel_id=channel_id,
        )
        return

    origin = route.get("origin")
    destination = route.get("destination")

    if not origin or not destination:
        logger.error("Route %r missing origin or destination — skipping", name)
        return

    mode = _search_mode(config, route)
    if mode == "flights_only":
        await _process_route_flights_only(
            route,
            config,
            dry_run=dry_run,
            bot_token=bot_token,
            channel_id=channel_id,
        )
        return

    if mode != "calendar_then_flights":
        logger.error("Route %r has unsupported search_mode %r — skipping", name, mode)
        return

    await _process_route_calendar_then_flights(
        route,
        config,
        dry_run=dry_run,
        bot_token=bot_token,
        channel_id=channel_id,
    )


def _validate_secrets(*, dry_run: bool, config: dict[str, Any], routes: list[Any]) -> None:
    if dry_run:
        return

    get_secret("DISCORD_BOT_TOKEN")
    get_secret("DISCORD_FLIGHT_SEARCH_CHANNEL_ID")

    modes = {_search_mode(config, route) for route in routes if isinstance(route, dict)}
    if "calendar_then_flights" in modes:
        get_secret("SEARCHAPI_KEY")
    if modes & {"calendar_then_flights", "flights_only"}:
        get_secret("SERPAPI_KEY")


async def _run_async(
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    path = config_path or DEFAULT_CONFIG_PATH
    try:
        config = _load_config(path)
    except Exception:
        logger.exception("Failed to load config from %s", path)
        return 1

    routes = config.get("routes") or []
    if not isinstance(routes, list) or not routes:
        logger.error("No routes configured in %s", path)
        return 1

    try:
        if dry_run:
            bot_token, channel_id = "", ""
        else:
            bot_token = get_secret("DISCORD_BOT_TOKEN")
            channel_id = get_secret("DISCORD_FLIGHT_SEARCH_CHANNEL_ID")
        _validate_secrets(dry_run=dry_run, config=config, routes=routes)
    except Exception:
        logger.exception("Missing required secrets for configured search modes")
        return 1

    for route in routes:
        if not isinstance(route, dict):
            logger.error("Skipping invalid route entry: %r", route)
            continue
        try:
            await _process_route(
                route,
                config,
                dry_run=dry_run,
                bot_token=bot_token,
                channel_id=channel_id,
            )
        except Exception:
            logger.exception(
                "Unhandled error processing route %r",
                route.get("name", "unnamed_route"),
            )

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Search flights via SearchAPI calendar + SerpApi and post to Discord.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single search-and-post cycle (default behavior).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve dates and quota without calling APIs or posting to Discord.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: skill-local config.yaml).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    return asyncio.run(_run_async(config_path=args.config, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
