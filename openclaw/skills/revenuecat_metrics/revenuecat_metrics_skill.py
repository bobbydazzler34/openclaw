"""RevenueCat daily metrics skill — API fetch, Obsidian log, Discord summary."""

from __future__ import annotations

import argparse
import logging
import traceback
from datetime import datetime, timezone

from openclaw.skills._base.skill_base import SkillBase
from openclaw.skills.revenuecat_metrics.config import load_config
from openclaw.skills.revenuecat_metrics.discord_notifier import send_summary_safe
from openclaw.skills.revenuecat_metrics.models import MetricsRunSummary, MetricsSnapshotModel
from openclaw.skills.revenuecat_metrics.obsidian_logger import format_daily_summary, write_summary
from openclaw.skills.revenuecat_metrics.revenuecat_client import (
    MetricsSnapshot,
    RevenueCatClient,
    resolve_dashlane_secret,
)

logger = logging.getLogger(__name__)


def _snapshot_to_model(snapshot: MetricsSnapshot) -> MetricsSnapshotModel:
    return MetricsSnapshotModel(
        active_trials=snapshot.active_trials,
        active_subscriptions=snapshot.active_subscriptions,
        mrr=snapshot.mrr,
        revenue_28d=snapshot.revenue_28d,
        new_customers=snapshot.new_customers,
        active_users=snapshot.active_users,
        snapshot_at=snapshot.snapshot_at,
        currency=snapshot.currency,
    )


def _fetch_metrics(
    *,
    api_key: str | None = None,
    project_id: str | None = None,
    client: RevenueCatClient | None = None,
) -> MetricsSnapshot:
    key = api_key or resolve_dashlane_secret("REVENUECAT_API_KEY")
    proj = project_id or resolve_dashlane_secret("REVENUECAT_PROJECT_ID")
    if client is not None:
        return client.fetch_overview_metrics()
    with RevenueCatClient(project_id=proj, api_key=key) as rc_client:
        return rc_client.fetch_overview_metrics()


class RevenueCatMetricsSkill(SkillBase):
    """Pull RevenueCat overview metrics and log to Obsidian + Discord."""

    def __init__(
        self,
        config_path: str | None = None,
        *,
        client: RevenueCatClient | None = None,
    ) -> None:
        super().__init__(config_path)
        self._client = client

    def run(self, *, dry_run: bool = False) -> MetricsRunSummary:
        """Fetch metrics, write Obsidian log, and post to Discord."""
        started = datetime.now(timezone.utc)
        errors: list[str] = []
        success = True
        snapshot: MetricsSnapshot | None = None
        obsidian_path: str | None = None
        discord_posted = False

        try:
            cfg = load_config(require_discord=not dry_run, require_obsidian=not dry_run)
            snapshot = _fetch_metrics(client=self._client)
            summary_text = format_daily_summary(snapshot)

            if dry_run:
                logger.info("Dry run — skipping Obsidian and Discord")
                return MetricsRunSummary(
                    snapshot=_snapshot_to_model(snapshot),
                    summary_text=summary_text,
                    success=True,
                    errors=[],
                    obsidian_path=None,
                    discord_posted=False,
                    dry_run=True,
                )

            out_path = write_summary(
                snapshot,
                vault_path=cfg.obsidian_vault_path,
                log_subfolder=cfg.skill_log_subfolder,
                success=True,
                errors=[],
            )
            if out_path is not None:
                obsidian_path = str(out_path)

            send_summary_safe(
                summary_text,
                bot_token=cfg.discord_bot_token,
                channel_id=cfg.discord_channel_id,
            )
            discord_posted = True

            return MetricsRunSummary(
                snapshot=_snapshot_to_model(snapshot),
                summary_text=summary_text,
                success=True,
                errors=[],
                obsidian_path=obsidian_path,
                discord_posted=discord_posted,
                dry_run=False,
            )
        except Exception as exc:  # noqa: BLE001
            success = False
            err_msg = f"{type(exc).__name__}: {exc}"
            errors.append(err_msg)
            logger.error("revenuecat_metrics failed: %s\n%s", err_msg, traceback.format_exc())

            if snapshot is None:
                snapshot = MetricsSnapshot(
                    active_trials=0,
                    active_subscriptions=0,
                    mrr=0.0,
                    revenue_28d=0.0,
                    new_customers=0,
                    active_users=0,
                    snapshot_at=started,
                )

            summary_text = format_daily_summary(snapshot)
            if not dry_run:
                try:
                    cfg = load_config(require_discord=False, require_obsidian=False)
                    out_path = write_summary(
                        snapshot,
                        vault_path=cfg.obsidian_vault_path,
                        log_subfolder=cfg.skill_log_subfolder,
                        success=False,
                        errors=errors,
                    )
                    if out_path is not None:
                        obsidian_path = str(out_path)
                except Exception as log_exc:  # noqa: BLE001
                    logger.warning("Failed to write failure Obsidian log: %s", log_exc)

            return MetricsRunSummary(
                snapshot=_snapshot_to_model(snapshot),
                summary_text=summary_text,
                success=success,
                errors=errors,
                obsidian_path=obsidian_path,
                discord_posted=discord_posted,
                dry_run=dry_run,
            )


def run(*, dry_run: bool = False) -> MetricsRunSummary:
    """Module entrypoint for OpenClaw / scripts."""
    return RevenueCatMetricsSkill().run(dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Fetch RevenueCat daily metrics.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the formatted summary without writing Obsidian or posting to Discord.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run(dry_run=args.dry_run)

    if args.dry_run:
        print(result.summary_text)
    else:
        print(result.model_dump_json(indent=2))

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
