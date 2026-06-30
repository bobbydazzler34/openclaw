"""Environment configuration for revenuecat_metrics (systemd / process env only)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from openclaw.skills.revenuecat_metrics.revenuecat_client import resolve_secret_reference


REQUIRED_ENV_VARS = (
    "REVENUECAT_API_KEY",
    "REVENUECAT_PROJECT_ID",
    "OBSIDIAN_VAULT_PATH",
    "SKILL_LOG_SUBFOLDER",
    "DISCORD_BOT_TOKEN",
    "DISCORD_REVENUECAT_METRICS_CHANNEL_ID",
)

@dataclass(frozen=True, slots=True)
class RevenueCatMetricsConfig:
    """Resolved configuration from environment variables."""

    obsidian_vault_path: str
    skill_log_subfolder: str
    discord_bot_token: str
    discord_channel_id: str


def load_config(*, require_discord: bool = True, require_obsidian: bool = True) -> RevenueCatMetricsConfig:
    """Load and validate configuration from ``os.environ``.

    Args:
        require_discord: When ``False`` (e.g. ``--dry-run``), Discord vars are optional.
        require_obsidian: When ``False`` (e.g. ``--dry-run``), Obsidian vars are optional.

    Returns:
        RevenueCatMetricsConfig with required values set.

    Raises:
        OSError: If any required variable is missing or empty.
    """
    required = list(REQUIRED_ENV_VARS)
    if not require_discord:
        required = [name for name in required if not name.startswith("DISCORD_")]
    if not require_obsidian:
        required = [
            name
            for name in required
            if name not in ("OBSIDIAN_VAULT_PATH", "SKILL_LOG_SUBFOLDER")
        ]

    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    if missing:
        lines = "\n".join(f"  - {name}" for name in missing)
        msg = f"Missing or empty required environment variables:\n{lines}"
        raise OSError(msg)

    discord_token = resolve_secret_reference((os.environ.get("DISCORD_BOT_TOKEN") or "").strip())
    discord_channel = resolve_secret_reference(
        (os.environ.get("DISCORD_REVENUECAT_METRICS_CHANNEL_ID") or "").strip(),
    )
    vault_path = (os.environ.get("OBSIDIAN_VAULT_PATH") or "").strip()
    log_subfolder = (os.environ.get("SKILL_LOG_SUBFOLDER") or "").strip()

    return RevenueCatMetricsConfig(
        obsidian_vault_path=vault_path,
        skill_log_subfolder=log_subfolder,
        discord_bot_token=discord_token,
        discord_channel_id=discord_channel,
    )
