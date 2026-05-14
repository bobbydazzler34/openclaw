"""LLM-based draft reply body for important emails (Gemini)."""

from __future__ import annotations

import logging

from openclaw.llm.gemini import generate_text
from openclaw.skills.gmail_triage.models import EmailMessage

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"

DRAFT_SYSTEM_PROMPT = (
    "Draft a professional, concise reply. Do not include a subject line. "
    "Do not sign off with a specific name — use '[Your name]' as placeholder. "
    "Keep it under 150 words."
)

SYSTEM = DRAFT_SYSTEM_PROMPT


def create_draft_response(email: EmailMessage, *, api_key: str) -> str:
    """Return plain-text draft body; on failure return empty string.

    Args:
        email: Full message including ``body_text``.
        api_key: Gemini API key.

    Returns:
        Draft body text, or empty string if generation fails.
    """
    user = (
        f"Subject: {email.subject}\n"
        f"From: {email.sender}\n\n"
        f"{email.body_text[:12000]}"
    )
    try:
        return generate_text(DRAFT_SYSTEM_PROMPT, user, model=MODEL, api_key=api_key).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drafter failed for email_id=%s: %s", email.id, type(exc).__name__)
        return ""
