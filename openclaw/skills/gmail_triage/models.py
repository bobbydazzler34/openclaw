"""Pydantic models for gmail_triage skill."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EmailMessage(BaseModel):
    """A Gmail message mapped for triage."""

    id: str
    thread_id: str
    subject: str
    sender: str
    received_at: datetime
    snippet: str
    body_text: str
    labels: list[str]
    rfc_message_id: str | None = Field(
        default=None,
        description="Message-ID header if present, for reply draft headers.",
    )


class ClassificationResult(BaseModel):
    """LLM classification for one email."""

    email_id: str
    classification: Literal["important", "deletable", "neutral"]
    reason: str
    suggested_reply: str | None = None


class EmailTriageLogEntry(BaseModel):
    """One line item for Obsidian / summary."""

    subject: str
    sender: str
    received_at: datetime
    reason: str


class TriageRunSummary(BaseModel):
    """Summary returned to OpenClaw / Telegram."""

    run_id: str
    account: str
    started_at: datetime
    completed_at: datetime
    emails_scanned: int
    drafts_created: int
    flagged_for_deletion: int
    errors: list[str]
    success: bool = True
    important_entries: list[EmailTriageLogEntry] = Field(default_factory=list)
    deletable_entries: list[EmailTriageLogEntry] = Field(default_factory=list)


ComposeStatus = Literal["drafted", "failed", "missing_recipient"]


class ComposedEmail(BaseModel):
    """Result of a natural-language compose (new outbound draft, not a reply)."""

    to: str | None
    subject: str
    body: str
    instruction: str
    composed_at: datetime
    draft_id: str | None = None
    status: ComposeStatus = "failed"
