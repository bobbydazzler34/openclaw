"""Gemini-powered new-email composer (JSON extraction, no Gmail send)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from openclaw.llm.gemini import generate_text
from openclaw.skills.gmail_triage.drafter import DRAFT_SYSTEM_PROMPT, MODEL
from openclaw.skills.gmail_triage.models import ComposedEmail

logger = logging.getLogger(__name__)

_MAX_INSTRUCTION_CHARS = 8000

_JSON_RULES = """
You must also output a single JSON object only (no markdown fences, no preamble) with exactly these keys:
  "to": string or null — the recipient email address if it appears explicitly in the instruction; otherwise null.
  "subject": string — email subject line.
  "body": string — plain-text email body (professional, concise; same tone as above).

Rules:
- If you cannot determine a recipient address from the instruction, set "to" to null. Never guess or invent an address.
- "subject" and "body" should still be reasonable even when "to" is null.
"""

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
)
# Addresses that appear literally in the instruction (lowercased set).
_INSTR_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.UNICODE)


def _emails_mentioned_in_text(text: str) -> set[str]:
    return {m.group(0).lower() for m in _INSTR_EMAIL_RE.finditer(text)}


def _sanitize_instruction(text: str) -> str:
    """Strip dangerous control characters and cap length."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()
    return cleaned[:_MAX_INSTRUCTION_CHARS]


def _valid_email(addr: str | None) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    candidate = addr.strip()
    return bool(_EMAIL_RE.match(candidate))


def _strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _compose_sync(safe_instruction: str, context: str | None, api_key: str, *, audit_instruction: str) -> ComposedEmail:
    """Blocking Gemini call + parse (run in thread from ``compose_email``)."""
    composed_at = datetime.now(timezone.utc)
    user_parts: dict[str, Any] = {"instruction": safe_instruction}
    if context:
        user_parts["context"] = context
    user = json.dumps(user_parts, ensure_ascii=False)
    system = f"{DRAFT_SYSTEM_PROMPT.strip()}\n\n{_JSON_RULES.strip()}"
    try:
        raw = generate_text(system, user, model=MODEL, api_key=api_key)
        text = _strip_json_fences(raw)
        data: dict[str, Any] = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Composer parse failed: %s", type(exc).__name__)
        return ComposedEmail(
            to=None,
            subject="",
            body="",
            instruction=audit_instruction,
            composed_at=composed_at,
            draft_id=None,
            status="failed",
        )

    to_raw = data.get("to")
    to_val: str | None
    if to_raw is None or (isinstance(to_raw, str) and not to_raw.strip()):
        to_val = None
    else:
        to_val = str(to_raw).strip() if _valid_email(str(to_raw).strip()) else None

    mentioned = _emails_mentioned_in_text(safe_instruction)
    if to_val and to_val.lower() not in mentioned:
        to_val = None

    subject = str(data.get("subject", "") or "").strip()
    body = str(data.get("body", "") or "").strip()

    if not to_val:
        return ComposedEmail(
            to=None,
            subject=subject,
            body=body,
            instruction=audit_instruction,
            composed_at=composed_at,
            draft_id=None,
            status="missing_recipient",
        )

    return ComposedEmail(
        to=to_val,
        subject=subject or "(no subject)",
        body=body,
        instruction=audit_instruction,
        composed_at=composed_at,
        draft_id=None,
        status="drafted",
    )


async def compose_email(
    instruction: str,
    context: str | None = None,
    *,
    api_key: str,
) -> ComposedEmail:
    """Use Gemini to extract recipient, subject, and body from natural language.

    Args:
        instruction: User prompt (e.g. from Telegram/Discord).
        context: Optional extra context.
        api_key: Gemini API key.

    Returns:
        ``ComposedEmail`` with ``status`` ``missing_recipient``, ``failed``, or
        ``drafted`` (meaning LLM produced a valid ``to`` — Gmail draft not yet created).
    """
    audit_instruction = instruction
    safe = _sanitize_instruction(instruction)
    if not safe:
        return ComposedEmail(
            to=None,
            subject="",
            body="",
            instruction=audit_instruction,
            composed_at=datetime.now(timezone.utc),
            draft_id=None,
            status="missing_recipient",
        )
    return await asyncio.to_thread(_compose_sync, safe, context, api_key, audit_instruction=audit_instruction)
