"""Maton gateway client for Gmail (native Gmail API paths, no send)."""

from __future__ import annotations

import base64
import email.policy
import logging
import re
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage as StdlibEmailMessage
from email.utils import formataddr, parseaddr
from typing import Any

import asyncio

import httpx

from openclaw.skills.gmail_triage.models import EmailMessage

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0
MAX_LIST_PAGES = 50
MAX_MESSAGES_CAP = 500
LIST_PAGE_SIZE = 100


class _RetryableStatus(Exception):
    """HTTP status that should trigger a retry."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"retryable status {status_code}")


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    return raw.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _headers_dict(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        if isinstance(h, dict) and h.get("name"):
            out[str(h["name"]).lower()] = str(h.get("value", ""))
    return out


def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (plain, html) body fragments from a MIME payload tree."""
    mime = str(payload.get("mimeType", "") or "")
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime == "text/plain":
        return _b64url_decode(str(data)), None
    if data and mime == "text/html":
        return None, _b64url_decode(str(data))
    plain: str | None = None
    html: str | None = None
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        p_plain, p_html = _extract_bodies(part)
        if p_plain:
            plain = p_plain
        if p_html:
            html = html or p_html
    return plain, html


def _message_to_email_message(data: dict[str, Any]) -> EmailMessage:
    """Map Gmail API message JSON to EmailMessage."""
    mid = str(data.get("id", ""))
    thread_id = str(data.get("threadId", ""))
    snippet = str(data.get("snippet", "") or "")
    labels = list(data.get("labelIds") or [])
    if not isinstance(labels, list):
        labels = []

    internal_date = data.get("internalDate")
    if internal_date is not None:
        ms = int(internal_date)
        received_at = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    else:
        received_at = datetime.now(timezone.utc)

    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    hdrs = _headers_dict(payload)
    subject = hdrs.get("subject", "(no subject)")
    sender = hdrs.get("from", "")
    rfc_message_id = hdrs.get("message-id") or None

    plain, html = _extract_bodies(payload)
    if plain:
        body_text = plain.strip()
    elif html:
        body_text = _strip_html(html)
    else:
        body_text = snippet

    return EmailMessage(
        id=mid,
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        received_at=received_at,
        snippet=snippet,
        body_text=body_text,
        labels=[str(x) for x in labels],
        rfc_message_id=rfc_message_id.strip() if rfc_message_id else None,
    )


def _messages_list_path(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/google-mail/gmail/v1/users/me/messages"


def _message_get_path(base_url: str, message_id: str) -> str:
    encoded = quote(message_id, safe="")
    return f"{base_url.rstrip('/')}/google-mail/gmail/v1/users/me/messages/{encoded}"


def _drafts_create_path(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/google-mail/gmail/v1/users/me/drafts"


def _assert_draft_url(path: str) -> None:
    """Ensure we never call a send endpoint by mistake."""
    lowered = path.lower()
    if "draft" not in lowered:
        msg = f"Expected draft endpoint path, got: {path}"
        raise AssertionError(msg)
    if "send" in lowered:
        msg = f"Refusing send-like path for draft creation: {path}"
        raise AssertionError(msg)


def _build_reply_mime(
    *,
    account_email: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
) -> str:
    """Build RFC 822 message string for a draft reply."""
    msg = StdlibEmailMessage(policy=email.policy.SMTP)
    msg["From"] = account_email
    msg["To"] = to_addr
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body, charset="utf-8", subtype="plain")
    return msg.as_string()


def _to_gmail_raw_b64url(rfc822: str) -> str:
    raw_bytes = rfc822.encode("utf-8")
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


class MatonGmailClient:
    """Async Gmail access via Maton API gateway (Bearer key, no send)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        account_email: str,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: Maton gateway base URL (no trailing slash required).
            api_key: Maton API key (Bearer).
            account_email: Mailbox address for logging / MIME From (gateway uses ``me``).
            timeout: HTTP timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._account_email = account_email
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform an HTTP request with retries for transient failures."""
        extra = kwargs.pop("headers", None) or {}
        merged_headers = {**self._headers, **extra}
        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=merged_headers,
                        **kwargs,
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    last_exc = _RetryableStatus(response.status_code)
                    await asyncio.sleep(min(20.0, 2.0**attempt))
                    continue
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                await asyncio.sleep(min(20.0, 2.0**attempt))
        if isinstance(last_exc, BaseException):
            raise last_exc
        raise RuntimeError("HTTP request failed after retries")

    async def fetch_recent_emails(self, hours: int = 24) -> list[EmailMessage]:
        """List and fetch Gmail messages from roughly the last ``hours`` hours.

        Uses ``GET .../users/me/messages`` with a broad ``q`` filter, then filters
        by ``internalDate`` for an hours-accurate window.

        Args:
            hours: Number of hours to look back (default 24).

        Returns:
            Parsed ``EmailMessage`` instances within the window.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        days = max(1, (hours + 23) // 24)
        q = f"newer_than:{days}d"

        list_url = _messages_list_path(self._base_url)
        page_token: str | None = None
        collected_ids: list[tuple[str, str]] = []
        pages = 0

        while pages < MAX_LIST_PAGES and len(collected_ids) < MAX_MESSAGES_CAP:
            pages += 1
            params: dict[str, Any] = {"maxResults": LIST_PAGE_SIZE, "q": q}
            if page_token:
                params["pageToken"] = page_token
            response = await self._request("GET", list_url, params=params)
            payload = response.json()
            messages = payload.get("messages") or []
            for m in messages:
                if isinstance(m, dict) and m.get("id"):
                    collected_ids.append((str(m["id"]), str(m.get("threadId", ""))))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        out: list[EmailMessage] = []
        for msg_id, _thread in collected_ids:
            if len(out) >= MAX_MESSAGES_CAP:
                break
            get_url = _message_get_path(self._base_url, msg_id)
            r = await self._request("GET", get_url, params={"format": "full"})
            data = r.json()
            em = _message_to_email_message(data)
            if int(data.get("internalDate", 0)) < cutoff_ms:
                continue
            out.append(em)

        logger.info("Fetched %s messages within last %s hours", len(out), hours)
        return out

    async def create_draft(
        self,
        account: str,
        reply_to_id: str,
        subject: str,
        body: str,
        *,
        thread_id: str | None = None,
        reply_to_sender: str | None = None,
        rfc_message_id: str | None = None,
    ) -> str:
        """Create a Gmail draft via Maton (never sends).

        Args:
            account: Mailbox address (for MIME ``From``; gateway uses authorized user).
            reply_to_id: Gmail message id being replied to.
            subject: Original subject (``Re:`` added if needed).
            body: Plain-text draft body.
            thread_id: Gmail thread id to keep reply in-thread.
            reply_to_sender: ``From`` header value of the original message.
            rfc_message_id: Original Message-ID header for threading headers.

        Returns:
            Gmail draft id string.

        Raises:
            AssertionError: If the resolved URL is not a safe drafts endpoint.
            httpx.HTTPError: On HTTP failure after retries.
        """
        path = _drafts_create_path(self._base_url)
        _assert_draft_url(path)

        _to = reply_to_sender or ""
        _, email_part = parseaddr(_to)
        if not email_part:
            email_part = _to.strip() or "unknown@invalid.local"

        in_reply = rfc_message_id
        if not in_reply and reply_to_id:
            in_reply = f"<{reply_to_id}@gmail.invalid>"
        refs = in_reply

        mime_str = _build_reply_mime(
            account_email=account or self._account_email,
            to_addr=formataddr(("", email_part)),
            subject=subject,
            body=body,
            in_reply_to=in_reply,
            references=refs,
        )
        raw = _to_gmail_raw_b64url(mime_str)
        message_body: dict[str, Any] = {"raw": raw}
        if thread_id:
            message_body["threadId"] = thread_id

        response = await self._request(
            "POST",
            path,
            json={"message": message_body},
            headers={**self._headers, "Content-Type": "application/json"},
        )
        result = response.json()
        draft_id = str(result.get("id", "") or "")
        if not draft_id:
            msg = "Gmail drafts.create response missing id"
            raise RuntimeError(msg)
        logger.info("Created draft id=%s for reply_to=%s", draft_id, reply_to_id)
        return draft_id
