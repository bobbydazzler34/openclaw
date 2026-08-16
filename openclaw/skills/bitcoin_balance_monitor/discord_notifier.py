"""Post Bitcoin balance monitor alerts to Discord."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


def _format_sats_btc(sats: int) -> str:
    btc = sats / 100_000_000
    return f"{sats:,} sats ({btc:.8f} BTC)"


def format_balance_change_message(
    *,
    label: str,
    old_total_sats: int,
    new_total_sats: int,
    old_confirmed_sats: int,
    new_confirmed_sats: int,
    old_unconfirmed_sats: int,
    new_unconfirmed_sats: int,
    timestamp: datetime | None = None,
) -> str:
    """Format a balance-change alert (labels only, no descriptor material)."""
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    delta = new_total_sats - old_total_sats
    direction = "increase" if delta > 0 else "decrease"

    lines = [
        f"BTC balance change — {label}",
        f"Time: {ts}",
        f"Direction: {direction} ({delta:+,} sats)",
        f"Previous total: {_format_sats_btc(old_total_sats)}",
        f"Current total:  {_format_sats_btc(new_total_sats)}",
        f"Previous confirmed / unconfirmed: {old_confirmed_sats:,} / {old_unconfirmed_sats:,} sats",
        f"Current confirmed / unconfirmed:  {new_confirmed_sats:,} / {new_unconfirmed_sats:,} sats",
    ]
    return "\n".join(lines)


def format_degraded_message(
    *,
    title: str,
    detail: str,
    timestamp: datetime | None = None,
) -> str:
    """Format a monitor degraded alert."""
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join(
        [
            f"BTC monitor DEGRADED — {title}",
            f"Time: {ts}",
            f"Error: {detail}",
        ],
    )


def send_message(
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
    payload = {"content": message[:2000]}

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()


def send_message_safe(
    message: str,
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> bool:
    """Send to Discord; log and swallow errors so posting does not fail the skill."""
    try:
        send_message(message, bot_token=bot_token, channel_id=channel_id, timeout=timeout)
        logger.info("Posted Bitcoin balance monitor alert to Discord channel %s", channel_id)
        return True
    except Exception:
        logger.exception("Failed to post Bitcoin balance monitor alert to Discord")
        return False
