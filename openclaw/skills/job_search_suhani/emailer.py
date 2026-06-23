"""Job digest sender via Maton Gmail API (messages.send)."""

from __future__ import annotations

import base64
import logging
import os

import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def _build_plain_text(jobs: list[dict]) -> str:
    lines = [f"{len(jobs)} new retail job listing(s):\n"]
    for job in jobs:
        lines.append(job["title"])
        lines.append(f"  {job.get('company', '')} — {job.get('location', '')}")
        lines.append(f"  {job.get('url', '')}\n")
    return "\n".join(lines)


def _build_html(jobs: list[dict]) -> str:
    rows = []
    for job in jobs:
        title = escape(job.get("title", ""))
        company = escape(job.get("company", ""))
        location = escape(job.get("location", ""))
        url = escape(job.get("url", ""), quote=True)
        rows.append(
            f'<tr><td style="padding:8px 0;border-bottom:1px solid #eee;">'
            f'<a href="{url}">{title}</a><br>'
            f'<span style="color:#555;">{company} — {location}</span>'
            f"</td></tr>"
        )
    body = "\n".join(rows)
    return (
        "<html><body>"
        f"<p>{len(jobs)} new retail job listing(s):</p>"
        f"<table>{body}</table>"
        "</body></html>"
    )


def _build_multipart_mime(
    from_addr: str,
    to: str,
    subject: str,
    plain: str,
    html: str,
) -> str:
    """Build RFC 822 multipart/alternative message string."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_string()


def _to_gmail_raw_b64url(rfc822: str) -> str:
    raw_bytes = rfc822.encode("utf-8")
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


def _maton_send_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/google-mail/gmail/v1/users/me/messages/send"


def _maton_send(raw_b64url: str) -> None:
    """POST base64url-encoded MIME to Maton Gmail messages.send."""
    api_key = os.environ.get("MATON_API_KEY")
    base_url = os.environ.get("MATON_BASE_URL")
    if not api_key or not base_url:
        msg = "MATON_API_KEY and MATON_BASE_URL environment variables are required"
        raise RuntimeError(msg)

    url = _maton_send_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.post(
        url,
        json={"raw": raw_b64url},
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def send_digest(
    jobs: list[dict],
    recipient: str,
    subject_prefix: str,
    *,
    allow_empty: bool = False,
) -> None:
    """Send an HTML + plain-text job digest via Maton Gmail send.

    Requires MATON_API_KEY, MATON_BASE_URL, and GMAIL_ACCOUNT_EMAIL environment
    variables. OAuth is handled by Maton — no Gmail App Password needed.

    No-op when ``jobs`` is empty unless ``allow_empty`` is True (sends a
    zero-new-listings notice).
    """
    if not jobs and not allow_empty:
        logger.info("No jobs to send — skipping email")
        return

    from_addr = os.environ.get("GMAIL_ACCOUNT_EMAIL")
    if not from_addr:
        msg = "GMAIL_ACCOUNT_EMAIL environment variable is required"
        raise RuntimeError(msg)

    if jobs:
        subject = f"{subject_prefix}: {len(jobs)} new listing(s)"
        plain = _build_plain_text(jobs)
        html = _build_html(jobs)
    else:
        subject = f"{subject_prefix}: 0 new listing(s)"
        plain = "No new retail job listings today."
        html = "<html><body><p>No new retail job listings today.</p></body></html>"

    mime_str = _build_multipart_mime(from_addr, recipient, subject, plain, html)
    raw_b64url = _to_gmail_raw_b64url(mime_str)

    logger.info("Sending digest to %s (%d job(s)) via Maton", recipient, len(jobs))
    try:
        _maton_send(raw_b64url)
    except requests.RequestException:
        logger.exception("Failed to send email digest to %s via Maton", recipient)
        raise

    logger.info("Email digest sent successfully")
