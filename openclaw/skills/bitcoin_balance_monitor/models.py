"""Data models for bitcoin_balance_monitor skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class ElectrumXSettings:
    """ElectrumX connection settings (non-secret)."""

    host: str
    port: int
    use_ssl: bool
    timeout_seconds: float
    allowed_hosts: frozenset[str]


@dataclass(frozen=True, slots=True)
class WalletConfig:
    """Watch-only wallet entry from config.yaml."""

    label: str
    descriptor_secret_name: str
    derivation_gap_limit: int = 20
    dust_threshold_sats: int = 0


@dataclass(frozen=True, slots=True)
class WalletBalance:
    """Balance snapshot for a single wallet."""

    label: str
    confirmed_sats: int
    unconfirmed_sats: int

    @property
    def total_sats(self) -> int:
        return self.confirmed_sats + self.unconfirmed_sats


@dataclass(frozen=True, slots=True)
class StoredWalletBalance:
    """Persisted balance state for one wallet."""

    confirmed_sats: int
    unconfirmed_sats: int
    total_sats: int
    last_checked_at: str


@dataclass
class WalletScanResult:
    """Outcome of scanning one wallet."""

    label: str
    balance: WalletBalance | None = None
    error: str | None = None
    alert_sent: bool = False
    alert_suppressed_dust: bool = False
    is_first_run: bool = False


@dataclass
class MonitorRunSummary:
    """Summary of a full monitor run."""

    success: bool = True
    degraded: bool = False
    wallet_results: list[WalletScanResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    obsidian_path: str | None = None
    discord_alerts_sent: int = 0
    checked_at: datetime | None = None


class MonitorRunSummaryModel(BaseModel):
    """Serializable run summary for CLI output."""

    success: bool = True
    degraded: bool = False
    errors: list[str] = Field(default_factory=list)
    obsidian_path: str | None = None
    discord_alerts_sent: int = 0
    wallet_labels: list[str] = Field(default_factory=list)
