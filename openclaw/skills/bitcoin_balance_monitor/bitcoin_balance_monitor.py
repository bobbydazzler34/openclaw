"""Bitcoin watch-only balance monitor — main orchestrator."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from openclaw.skills._base.skill_base import SkillBase
from openclaw.skills.bitcoin_balance_monitor.config import DEFAULT_CONFIG_PATH, MonitorConfig, load_config
from openclaw.skills.bitcoin_balance_monitor.discord_notifier import (
    format_balance_change_message,
    format_degraded_message,
    send_message_safe,
)
from openclaw.skills.bitcoin_balance_monitor.electrumx_client import ElectrumXClient, ElectrumXError
from openclaw.skills.bitcoin_balance_monitor.models import (
    MonitorRunSummary,
    MonitorRunSummaryModel,
    WalletScanResult,
)
from openclaw.skills.bitcoin_balance_monitor.obsidian_logger import write_summary
from openclaw.skills.bitcoin_balance_monitor.state_store import StateStore
from openclaw.skills.bitcoin_balance_monitor.wallet_scanner import WalletScanError, scan_wallet

logger = logging.getLogger(__name__)


class BitcoinBalanceMonitorSkill(SkillBase):
    """Monitor watch-only Bitcoin wallets via local ElectrumX."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        electrumx_client: ElectrumXClient | None = None,
    ) -> None:
        super().__init__(config_path)
        self._electrumx_client = electrumx_client

    def run(self, *, dry_run: bool = False) -> MonitorRunSummary:
        """Scan configured wallets, compare to state, alert on changes."""
        checked_at = datetime.now(timezone.utc)
        config_path = self.config.get("_config_path")

        try:
            cfg = load_config(
                config_path,
                require_discord=not dry_run,
                require_obsidian=not dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            summary = MonitorRunSummary(checked_at=checked_at, success=False, degraded=True)
            err = f"{type(exc).__name__}: {exc}"
            summary.errors.append(err)
            logger.error("Config load failed: %s", err)
            if not dry_run:
                self._send_degraded_if_possible(
                    title="configuration error",
                    detail=err,
                    summary=summary,
                )
            return summary

        return self._run_with_config(cfg, dry_run=dry_run, checked_at=checked_at)

    def _run_with_config(
        self,
        cfg: MonitorConfig,
        *,
        dry_run: bool,
        checked_at: datetime,
    ) -> MonitorRunSummary:
        summary = MonitorRunSummary(checked_at=checked_at)
        state_store = StateStore(cfg.state_path)
        prior_state = state_store.load()

        client = self._electrumx_client
        owns_client = client is None

        try:
            if owns_client:
                client = ElectrumXClient(cfg.electrumx)
                client.connect()
                client.ping()
            else:
                client.ping()

            for wallet in cfg.wallets:
                result, alerts_sent = self._process_wallet(
                    wallet,
                    client=client,
                    state_store=state_store,
                    prior_state=prior_state,
                    cfg=cfg,
                    dry_run=dry_run,
                    checked_at=checked_at,
                )
                summary.wallet_results.append(result)
                summary.discord_alerts_sent += alerts_sent
                if result.error:
                    summary.degraded = True
                    summary.errors.append(f"{wallet.label}: {result.error}")

        except ElectrumXError as exc:
            summary.success = False
            summary.degraded = True
            err = str(exc)
            summary.errors.append(err)
            logger.error("ElectrumX error: %s", err)
            if not dry_run:
                detail = f"{cfg.electrumx.host}:{cfg.electrumx.port} — {err}"
                if send_message_safe(
                    format_degraded_message(title="ElectrumX unreachable", detail=detail),
                    bot_token=cfg.discord_bot_token,
                    channel_id=cfg.discord_channel_id,
                ):
                    summary.discord_alerts_sent += 1

        except Exception as exc:  # noqa: BLE001
            summary.success = False
            summary.degraded = True
            err = f"{type(exc).__name__}: {exc}"
            summary.errors.append(err)
            logger.error("Monitor run failed: %s\n%s", err, traceback.format_exc())
            if not dry_run:
                if send_message_safe(
                    format_degraded_message(title="unexpected error", detail=err),
                    bot_token=cfg.discord_bot_token,
                    channel_id=cfg.discord_channel_id,
                ):
                    summary.discord_alerts_sent += 1

        finally:
            if owns_client and client is not None:
                client.close()

        if summary.degraded:
            summary.success = False

        if not dry_run:
            out_path = write_summary(
                summary,
                vault_path=cfg.obsidian_vault_path,
                log_subfolder=cfg.skill_log_subfolder,
            )
            if out_path is not None:
                summary.obsidian_path = str(out_path)

        return summary

    def _process_wallet(
        self,
        wallet,
        *,
        client: ElectrumXClient,
        state_store: StateStore,
        prior_state: dict,
        cfg: MonitorConfig,
        dry_run: bool,
        checked_at: datetime,
    ) -> tuple[WalletScanResult, int]:
        result = WalletScanResult(label=wallet.label)
        alerts_sent = 0

        try:
            balance = scan_wallet(wallet, client)
            result.balance = balance
        except WalletScanError as exc:
            result.error = str(exc)
            logger.error("Wallet scan failed for %r: %s", wallet.label, exc)
            if not dry_run and send_message_safe(
                format_degraded_message(title=wallet.label, detail=str(exc)),
                bot_token=cfg.discord_bot_token,
                channel_id=cfg.discord_channel_id,
            ):
                alerts_sent = 1
            return result, alerts_sent

        previous = prior_state.get(wallet.label)
        if previous is None:
            result.is_first_run = True
            logger.info("First run for %r — recording baseline", wallet.label)
            if not dry_run:
                state_store.save_wallet(balance, checked_at=checked_at)
            return result, alerts_sent

        delta = abs(balance.total_sats - previous.total_sats)
        if delta > wallet.dust_threshold_sats:
            message = format_balance_change_message(
                label=wallet.label,
                old_total_sats=previous.total_sats,
                new_total_sats=balance.total_sats,
                old_confirmed_sats=previous.confirmed_sats,
                new_confirmed_sats=balance.confirmed_sats,
                old_unconfirmed_sats=previous.unconfirmed_sats,
                new_unconfirmed_sats=balance.unconfirmed_sats,
                timestamp=checked_at,
            )
            if dry_run:
                logger.info("Dry run — would alert for %r: delta=%d sats", wallet.label, delta)
            elif send_message_safe(
                message,
                bot_token=cfg.discord_bot_token,
                channel_id=cfg.discord_channel_id,
            ):
                result.alert_sent = True
                alerts_sent = 1
        elif delta > 0:
            result.alert_suppressed_dust = True
            logger.info(
                "Dust change suppressed for %r: delta=%d sats (threshold=%d)",
                wallet.label,
                delta,
                wallet.dust_threshold_sats,
            )

        if not dry_run:
            state_store.save_wallet(balance, checked_at=checked_at)

        return result, alerts_sent

    def _send_degraded_if_possible(
        self,
        *,
        title: str,
        detail: str,
        summary: MonitorRunSummary,
    ) -> None:
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        channel = os.environ.get("DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID", "").strip()
        if token and channel:
            if send_message_safe(
                format_degraded_message(title=title, detail=detail),
                bot_token=token,
                channel_id=channel,
            ):
                summary.discord_alerts_sent += 1


def run(*, dry_run: bool = False, config_path: Path | str | None = None) -> MonitorRunSummary:
    """Module entrypoint for OpenClaw / scripts."""
    skill = BitcoinBalanceMonitorSkill()
    skill.config["_config_path"] = str(config_path or DEFAULT_CONFIG_PATH)
    return skill.run(dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Monitor watch-only Bitcoin wallet balances.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan wallets without updating state, Obsidian logs, or Discord alerts.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: skill directory config.yaml).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    result = run(dry_run=args.dry_run, config_path=args.config)

    print(
        MonitorRunSummaryModel(
            success=result.success,
            degraded=result.degraded,
            errors=result.errors,
            obsidian_path=result.obsidian_path,
            discord_alerts_sent=result.discord_alerts_sent,
            wallet_labels=[wr.label for wr in result.wallet_results],
        ).model_dump_json(indent=2),
    )

    if result.degraded:
        return 1
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
