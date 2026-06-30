"""Post RevenueCat metrics summaries to Discord."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


def send_summary(
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


def send_summary_safe(
    message: str,
    *,
    bot_token: str,
    channel_id: str,
    timeout: float = 30.0,
) -> None:
    """Send to Discord; log and swallow errors so posting does not fail the skill."""
    try:
        send_summary(message, bot_token=bot_token, channel_id=channel_id, timeout=timeout)
        logger.info("Posted RevenueCat metrics summary to Discord channel %s", channel_id)
    except Exception:
        logger.exception("Failed to post RevenueCat metrics summary to Discord")
