"""LLM-based email importance classification using Gemini."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openclaw.llm.gemini import generate_text
from openclaw.skills.gmail_triage.models import ClassificationResult, EmailMessage

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"

SYSTEM = """You classify a single email for triage.
Respond with JSON only, no markdown, in this exact shape:
{"classification":"important"|"deletable"|"neutral","reason":"one sentence","suggested_reply":string|null}

Definitions:
- important: needs a human reply (clients, colleagues, time-sensitive asks).
- deletable: newsletters, marketing, automated notifications, clear spam patterns.
- neutral: FYI, receipts, no reply needed.

For suggested_reply: a one-line optional hint only for important; otherwise null."""


def classify_email(email: EmailMessage, *, api_key: str) -> ClassificationResult:
    """Classify an email; on failure return neutral with a safe reason.

    Args:
        email: Parsed Gmail message (snippet only in prompt, not full body).
        api_key: Gemini API key.

    Returns:
        ``ClassificationResult`` (never raises for model/parse errors).
    """
    user = json.dumps(
        {
            "email_id": email.id,
            "subject": email.subject,
            "snippet": email.snippet[:2000],
            "sender": email.sender,
        },
        ensure_ascii=False,
    )
    try:
        raw = generate_text(SYSTEM, user, model=MODEL, api_key=api_key)
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        data: dict[str, Any] = json.loads(text)
        cls = str(data.get("classification", "neutral")).lower()
        if cls not in ("important", "deletable", "neutral"):
            cls = "neutral"
        reason = str(data.get("reason", "") or "Unable to parse model reason.")[:2000]
        suggested = data.get("suggested_reply")
        suggested_reply = str(suggested).strip() if suggested else None
        if cls != "important":
            suggested_reply = None
        return ClassificationResult(
            email_id=email.id,
            classification=cls,  # type: ignore[arg-type]
            reason=reason,
            suggested_reply=suggested_reply,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Classifier failed for email_id=%s: %s", email.id, type(exc).__name__)
        return ClassificationResult(
            email_id=email.id,
            classification="neutral",
            reason="Classification failed; defaulting to neutral.",
            suggested_reply=None,
        )
