"""Pydantic models for revenuecat_metrics skill."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MetricsSnapshotModel(BaseModel):
    """Serializable overview metrics snapshot."""

    active_trials: int
    active_subscriptions: int
    mrr: float
    revenue_28d: float
    new_customers: int
    active_users: int
    snapshot_at: datetime
    currency: str | None = None


class MetricsRunSummary(BaseModel):
    """Summary returned to OpenClaw / scripts."""

    snapshot: MetricsSnapshotModel
    summary_text: str
    success: bool = True
    errors: list[str] = Field(default_factory=list)
    obsidian_path: str | None = None
    discord_posted: bool = False
    dry_run: bool = False
