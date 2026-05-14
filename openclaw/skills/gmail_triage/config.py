"""Environment configuration for gmail_triage (systemd / process env only)."""

from __future__ import annotations

import os
from dataclasses import dataclass


REQUIRED_ENV_VARS = (
    "MATON_API_KEY",
    "MATON_BASE_URL",
    "GMAIL_ACCOUNT_EMAIL",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "GEMINI_API_KEY",
    "OBSIDIAN_VAULT_PATH",
    "SKILL_LOG_SUBFOLDER",
)


@dataclass(frozen=True, slots=True)
class GmailTriageConfig:
    """Resolved configuration from environment variables."""

    maton_api_key: str
    maton_base_url: str
    gmail_account_email: str
    supabase_url: str
    supabase_service_key: str
    gemini_api_key: str
    obsidian_vault_path: str
    skill_log_subfolder: str


def load_config() -> GmailTriageConfig:
    """Load and validate configuration from ``os.environ``.

    Returns:
        GmailTriageConfig with all required values set.

    Raises:
        OSError: If any required variable is missing or empty.
    """
    missing = [name for name in REQUIRED_ENV_VARS if not (os.environ.get(name) or "").strip()]
    if missing:
        lines = "\n".join(f"  - {name}" for name in missing)
        msg = f"Missing or empty required environment variables:\n{lines}"
        raise OSError(msg)
    return GmailTriageConfig(
        maton_api_key=os.environ["MATON_API_KEY"].strip(),
        maton_base_url=os.environ["MATON_BASE_URL"].strip().rstrip("/"),
        gmail_account_email=os.environ["GMAIL_ACCOUNT_EMAIL"].strip(),
        supabase_url=os.environ["SUPABASE_URL"].strip().rstrip("/"),
        supabase_service_key=os.environ["SUPABASE_SERVICE_KEY"].strip(),
        gemini_api_key=os.environ["GEMINI_API_KEY"].strip(),
        obsidian_vault_path=os.environ["OBSIDIAN_VAULT_PATH"].strip(),
        skill_log_subfolder=os.environ["SKILL_LOG_SUBFOLDER"].strip(),
    )
