"""Supabase persistence for gmail_triage (sync client, idempotent upserts)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from supabase import Client, create_client
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the error is likely transient."""
    name = type(exc).__name__
    if "RemoteProtocolError" in name or "ConnectError" in name or "Timeout" in name:
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "connection" in msg or "temporarily" in msg


class _RetryableSupabase(Exception):
    """Marker for transient Supabase / HTTP failures."""


def _reraise_if_retryable(exc: BaseException) -> None:
    if _is_retryable(exc):
        raise _RetryableSupabase(str(exc)) from exc
    raise exc


class GmailTriageStore:
    """Read/write gmail triage tables via Supabase service role."""

    def __init__(self, url: str, service_key: str) -> None:
        """Create a store using the Supabase project URL and service role key.

        Args:
            url: Supabase project URL (``https://....supabase.co``).
            service_key: Service role key (bypasses RLS; keep secret).
        """
        self._client: Client = create_client(url, service_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type(_RetryableSupabase),
        reraise=True,
    )
    def _safe_call(self, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` with retries on transient errors."""
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — narrow via _is_retryable
            _reraise_if_retryable(exc)
            raise

    def insert_run_running(self, account: str) -> str:
        """Insert a run row with ``status='running'`` and return its UUID."""
        payload = {"account": account, "status": "running"}
        resp = self._safe_call(
            lambda: self._client.table("gmail_triage_runs").insert(payload).execute(),
        )
        rows = getattr(resp, "data", None) or []
        if not rows:
            msg = "Supabase insert_run returned no rows"
            raise RuntimeError(msg)
        run_id = str(rows[0]["id"])
        logger.info("Started gmail_triage run id=%s", run_id)
        return run_id

    def update_run_success(
        self,
        run_id: str,
        *,
        emails_scanned: int,
        drafts_created: int,
        flagged_delete: int,
    ) -> None:
        """Mark run successful with final counters."""
        completed = datetime.now(timezone.utc).isoformat()
        payload = {
            "status": "success",
            "completed_at": completed,
            "emails_scanned": emails_scanned,
            "drafts_created": drafts_created,
            "flagged_delete": flagged_delete,
        }
        self._safe_call(
            lambda: self._client.table("gmail_triage_runs").update(payload).eq("id", run_id).execute(),
        )

    def update_run_failed(self, run_id: str, error_message: str) -> None:
        """Mark run failed with an error message."""
        completed = datetime.now(timezone.utc).isoformat()
        payload = {
            "status": "failed",
            "completed_at": completed,
            "error_message": error_message[:8000],
        }
        self._safe_call(
            lambda: self._client.table("gmail_triage_runs").update(payload).eq("id", run_id).execute(),
        )

    def scan_exists(self, email_id: str) -> bool:
        """Return True if this Gmail message id was already recorded."""
        resp = self._safe_call(
            lambda: self._client.table("gmail_triage_scans")
            .select("email_id")
            .eq("email_id", email_id)
            .limit(1)
            .execute(),
        )
        rows = getattr(resp, "data", None) or []
        return len(rows) > 0

    def upsert_scan(self, row: dict[str, Any]) -> None:
        """Upsert a scan row on ``email_id`` conflict."""
        self._safe_call(
            lambda: self._client.table("gmail_triage_scans")
            .upsert(row, on_conflict="email_id")
            .execute(),
        )
