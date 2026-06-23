"""Persist check results and send Telegram failure alerts."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from openclaw.secrets import get_secret
from openclaw.skills.farefynder_monitor.checker import CheckResult

logger = logging.getLogger(__name__)


async def record_result(
    *,
    check_name: str,
    result: CheckResult,
    timeout_seconds: float,
) -> None:
    """Write a row to monitoring.check_results via PostgREST."""
    supabase_url = get_secret("SUPABASE_URL").rstrip("/")
    service_role_key = get_secret("SUPABASE_SERVICE_ROLE_KEY")

    payload: dict[str, Any] = {
        "check_name": check_name,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "error_detail": result.error_detail,
        "raw_response": result.raw_response,
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{supabase_url}/rest/v1/check_results",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
                "Content-Profile": "monitoring",
                "Prefer": "return=minimal",
            },
            json=payload,
        )
        response.raise_for_status()


async def send_failure_alert(
    *,
    check_name: str,
    result: CheckResult,
    timeout_seconds: float,
) -> None:
    """Send an immediate Telegram alert for a failed check."""
    bot_token = get_secret("TELEGRAM_BOT_TOKEN")
    chat_id = get_secret("TELEGRAM_CHAT_ID")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    error_detail = result.error_detail or "unknown error"

    lines = [
        "🚨 FareFynder Auth Check FAILED",
        f"Check: {check_name}",
        f"Time: {timestamp}",
        f"Error: {error_detail}",
    ]
    if result.latency_ms is not None:
        lines.append(f"Latency: {result.latency_ms}ms")

    message = "\n".join(lines)

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        )
        response.raise_for_status()


async def record_result_safe(
    *,
    check_name: str,
    result: CheckResult,
    timeout_seconds: float,
) -> None:
    try:
        await record_result(
            check_name=check_name,
            result=result,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        logger.exception("Failed to record check result to Supabase")


async def send_failure_alert_safe(
    *,
    check_name: str,
    result: CheckResult,
    timeout_seconds: float,
) -> None:
    try:
        await send_failure_alert(
            check_name=check_name,
            result=result,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        logger.exception("Failed to send Telegram failure alert")
