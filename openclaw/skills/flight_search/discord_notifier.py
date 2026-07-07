"""Post flight search results to Discord via the Sempiternal bot token."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
FLIGHTS_EMBED_COLOR = 0x3498DB
CALENDAR_EMBED_COLOR = 0x2ECC71
MAX_EMBED_DESCRIPTION = 4096
MAX_MESSAGE_CONTENT = 2000


async def send_message(
    message: str,
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> None:
    """Send a plain-text message to a Discord channel."""
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    payload = {"content": message[:MAX_MESSAGE_CONTENT]}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()


async def send_embed(
    embed: dict[str, Any],
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> None:
    """Send a single embed to a Discord channel."""
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    payload = {"embeds": [embed]}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()


async def send_message_safe(
    message: str,
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> None:
    """Send to Discord; log and swallow errors so posting does not fail the skill."""
    try:
        await send_message(
            message,
            bot_token=bot_token,
            channel_id=channel_id,
            timeout=timeout,
        )
        logger.info("Posted message to Discord channel %s", channel_id)
    except Exception:
        logger.exception("Failed to post message to Discord")


async def send_embed_safe(
    embed: dict[str, Any],
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> None:
    """Send an embed to Discord; log and swallow errors so posting does not fail the skill."""
    try:
        await send_embed(
            embed,
            bot_token=bot_token,
            channel_id=channel_id,
            timeout=timeout,
        )
        logger.info("Posted flight search embed to Discord channel %s", channel_id)
    except Exception:
        logger.exception("Failed to post flight search embed to Discord")


def _truncate_description(description: str) -> str:
    if len(description) > MAX_EMBED_DESCRIPTION:
        return description[: MAX_EMBED_DESCRIPTION - 3] + "..."
    return description


def build_calendar_embed(
    *,
    origin: str,
    destination: str,
    window_label: str,
    calendar_lines: list[str],
) -> dict[str, Any]:
    """Build a Discord embed for calendar price results."""
    title = f"{origin} → {destination} — calendar {window_label}"
    description = "\n".join(calendar_lines) if calendar_lines else "No calendar prices found."
    return {
        "title": title,
        "description": _truncate_description(description),
        "color": CALENDAR_EMBED_COLOR,
    }


def build_route_embed(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None,
    trip_type: str,
    flight_blocks: list[str],
    route_sequence: str | None = None,
    leg_dates: list[str] | None = None,
    google_flights_url: str | None = None,
    footer: str | None = None,
    thumbnail_url: str | None = None,
) -> dict[str, Any]:
    """Build a Discord embed for a route's flight results."""
    if route_sequence:
        title = route_sequence
        if leg_dates:
            title = f"{route_sequence} — {' · '.join(leg_dates)}"
    elif trip_type == "round_trip" and return_date:
        title = f"{origin} → {destination} — {outbound_date} ↔ {return_date}"
    else:
        title = f"{origin} → {destination} — {outbound_date}"

    description = "\n\n".join(flight_blocks) if flight_blocks else "No flights found."

    embed: dict[str, Any] = {
        "title": title,
        "description": _truncate_description(description),
        "color": FLIGHTS_EMBED_COLOR,
    }
    if google_flights_url:
        embed["url"] = google_flights_url
    if footer:
        embed["footer"] = {"text": footer[:2048]}
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    return embed


def build_multi_city_calendar_embed(
    *,
    route_sequence: str,
    window_label: str,
    calendar_lines: list[str],
) -> dict[str, Any]:
    """Build a Discord embed for leg-1 calendar prices on a multi-city route."""
    title = f"{route_sequence} — leg 1 calendar {window_label}"
    description = "\n".join(calendar_lines) if calendar_lines else "No calendar prices found."
    return {
        "title": title,
        "description": _truncate_description(description),
        "color": CALENDAR_EMBED_COLOR,
    }
