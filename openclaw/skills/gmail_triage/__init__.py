"""Gmail triage skill package."""

from __future__ import annotations

from openclaw.skills.gmail_triage.skill import (
    GmailTriageSkill,
    format_compose_reply,
    run,
    run_compose,
    run_compose_sync,
)

__all__ = [
    "GmailTriageSkill",
    "format_compose_reply",
    "run",
    "run_compose",
    "run_compose_sync",
]
