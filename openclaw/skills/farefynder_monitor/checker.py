"""Synthetic Supabase Auth sign-in and token refresh check."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from openclaw.secrets import get_secret

CheckStatus = Literal["pass", "fail"]

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "token",
        "password",
        "email",
        "provider_token",
        "provider_refresh_token",
    }
)


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    latency_ms: int | None
    error_detail: str | None
    raw_response: dict[str, Any] | None


def _redact_auth_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_KEYS:
            if isinstance(value, str) and value:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        elif isinstance(value, dict):
            redacted[key] = _redact_auth_payload(value)
        else:
            redacted[key] = value
    return redacted


def _auth_headers(anon_key: str) -> dict[str, str]:
    return {
        "apikey": anon_key,
        "Content-Type": "application/json",
    }


def _validate_access_token(payload: dict[str, Any], step: str) -> str | None:
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return f"{step}: response missing non-empty access_token"
    return None


async def run_auth_check(*, timeout_seconds: float) -> CheckResult:
    """Sign in with email/password, refresh the token, and measure total latency."""
    supabase_url = get_secret("SUPABASE_URL").rstrip("/")
    anon_key = get_secret("SUPABASE_ANON_KEY")
    email = get_secret("FAREFYNDER_MONITOR_EMAIL")
    password = get_secret("FAREFYNDER_MONITOR_PASSWORD")

    sign_in_url = f"{supabase_url}/auth/v1/token?grant_type=password"
    refresh_url = f"{supabase_url}/auth/v1/token?grant_type=refresh_token"
    headers = _auth_headers(anon_key)
    raw_response: dict[str, Any] = {}

    started_at: float | None = None

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            started_at = time.perf_counter()
            sign_in_resp = await client.post(
                sign_in_url,
                headers=headers,
                json={"email": email, "password": password},
            )
            raw_response["sign_in"] = {
                "status_code": sign_in_resp.status_code,
                "has_access_token": False,
                "has_refresh_token": False,
            }

            if sign_in_resp.status_code < 200 or sign_in_resp.status_code >= 300:
                return CheckResult(
                    status="fail",
                    latency_ms=None,
                    error_detail=f"sign-in failed with HTTP {sign_in_resp.status_code}",
                    raw_response=raw_response,
                )

            try:
                sign_in_body = sign_in_resp.json()
            except ValueError:
                return CheckResult(
                    status="fail",
                    latency_ms=None,
                    error_detail="sign-in response was not valid JSON",
                    raw_response=raw_response,
                )

            if not isinstance(sign_in_body, dict):
                return CheckResult(
                    status="fail",
                    latency_ms=None,
                    error_detail="sign-in response JSON was not an object",
                    raw_response=raw_response,
                )

            raw_response["sign_in"]["body"] = _redact_auth_payload(sign_in_body)
            raw_response["sign_in"]["has_access_token"] = bool(
                sign_in_body.get("access_token")
            )
            raw_response["sign_in"]["has_refresh_token"] = bool(
                sign_in_body.get("refresh_token")
            )

            token_error = _validate_access_token(sign_in_body, "sign-in")
            if token_error:
                return CheckResult(
                    status="fail",
                    latency_ms=None,
                    error_detail=token_error,
                    raw_response=raw_response,
                )

            refresh_token = sign_in_body.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token.strip():
                return CheckResult(
                    status="fail",
                    latency_ms=None,
                    error_detail="sign-in response missing non-empty refresh_token",
                    raw_response=raw_response,
                )

            refresh_resp = await client.post(
                refresh_url,
                headers=headers,
                json={"refresh_token": refresh_token},
            )
            raw_response["refresh"] = {
                "status_code": refresh_resp.status_code,
                "has_access_token": False,
            }

            if refresh_resp.status_code < 200 or refresh_resp.status_code >= 300:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                return CheckResult(
                    status="fail",
                    latency_ms=latency_ms,
                    error_detail=f"token refresh failed with HTTP {refresh_resp.status_code}",
                    raw_response=raw_response,
                )

            try:
                refresh_body = refresh_resp.json()
            except ValueError:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                return CheckResult(
                    status="fail",
                    latency_ms=latency_ms,
                    error_detail="token refresh response was not valid JSON",
                    raw_response=raw_response,
                )

            if not isinstance(refresh_body, dict):
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                return CheckResult(
                    status="fail",
                    latency_ms=latency_ms,
                    error_detail="token refresh response JSON was not an object",
                    raw_response=raw_response,
                )

            raw_response["refresh"]["body"] = _redact_auth_payload(refresh_body)
            raw_response["refresh"]["has_access_token"] = bool(
                refresh_body.get("access_token")
            )

            token_error = _validate_access_token(refresh_body, "token refresh")
            if token_error:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                return CheckResult(
                    status="fail",
                    latency_ms=latency_ms,
                    error_detail=token_error,
                    raw_response=raw_response,
                )

            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return CheckResult(
                status="pass",
                latency_ms=latency_ms,
                error_detail=None,
                raw_response=raw_response,
            )

    except httpx.TimeoutException:
        return CheckResult(
            status="fail",
            latency_ms=None,
            error_detail="HTTP request timed out",
            raw_response=raw_response or None,
        )
    except httpx.HTTPError as exc:
        return CheckResult(
            status="fail",
            latency_ms=None,
            error_detail=f"HTTP error: {exc.__class__.__name__}",
            raw_response=raw_response or None,
        )
