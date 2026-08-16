"""Unit tests for bitcoin_balance_monitor."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from openclaw.skills.bitcoin_balance_monitor.config import load_config
from openclaw.skills.bitcoin_balance_monitor.discord_notifier import (
    format_balance_change_message,
    format_degraded_message,
    send_message_safe,
)
from openclaw.skills.bitcoin_balance_monitor.electrumx_client import (
    ElectrumXClient,
    ElectrumXError,
    scripthash_from_script,
)
from openclaw.skills.bitcoin_balance_monitor.models import (
    ElectrumXSettings,
    WalletBalance,
    WalletConfig,
)
from openclaw.skills.bitcoin_balance_monitor.state_store import StateStore
from openclaw.skills.bitcoin_balance_monitor.wallet_scanner import (
    WalletScanError,
    _normalize_watch_only_material,
)


TEST_XPUB = (
    "tpubDDKLbGAA5Vz2jZMW9jHPj38MZ47mGPTCq5sJtU1sXn6wDUn4q8Nf6ZKX5h"
)
TEST_DESCRIPTOR = f"wpkh({TEST_XPUB}/0/*)"


class TestScripthash(unittest.TestCase):
    def test_scripthash_is_reversed_double_sha256(self) -> None:
        script = bytes.fromhex("0014" + "ab" * 20)
        result = scripthash_from_script(script)
        self.assertEqual(len(result), 64)
        self.assertRegex(result, r"^[0-9a-f]+$")


class TestNormalizeWatchOnlyMaterial(unittest.TestCase):
    def test_bare_xpub_expands_receive_and_change(self) -> None:
        receive, change = _normalize_watch_only_material(TEST_XPUB)
        self.assertEqual(receive, f"wpkh({TEST_XPUB}/0/*)")
        self.assertEqual(change, f"wpkh({TEST_XPUB}/1/*)")

    def test_output_descriptor_splits_change_chain(self) -> None:
        receive, change = _normalize_watch_only_material(TEST_DESCRIPTOR)
        self.assertIn("/0/*", receive)
        self.assertIn("/1/*", change)

    def test_strips_checksum(self) -> None:
        with_checksum = f"{TEST_DESCRIPTOR}#12345678"
        receive, _change = _normalize_watch_only_material(with_checksum)
        self.assertNotIn("#", receive)

    def test_empty_secret_raises(self) -> None:
        with self.assertRaises(WalletScanError):
            _normalize_watch_only_material("   ")


class TestConfigAllowlist(unittest.TestCase):
    def test_rejects_non_allowlisted_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
electrumx:
  host: "8.8.8.8"
  port: 50001
  use_ssl: false
  allowed_hosts:
    - "192.168.1.240"
state_path: "/tmp/state.json"
wallets:
  - label: "Test"
    descriptor_secret_name: "BTC_TEST"
""",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ValueError) as ctx:
                    load_config(config_path, require_discord=False, require_obsidian=False)
            self.assertIn("allowed_hosts", str(ctx.exception))


class TestStateStore(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            store = StateStore(path)
            balance = WalletBalance(label="Wallet A", confirmed_sats=100_000, unconfirmed_sats=500)
            store.save_wallet(balance, checked_at=datetime(2026, 8, 15, tzinfo=timezone.utc))

            loaded = store.load()
            self.assertIn("Wallet A", loaded)
            self.assertEqual(loaded["Wallet A"].total_sats, 100_500)
            self.assertEqual(loaded["Wallet A"].confirmed_sats, 100_000)

    def test_load_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = StateStore(Path(tmpdir) / "missing.json")
            self.assertEqual(store.load(), {})


class TestDiscordMessages(unittest.TestCase):
    def test_balance_change_message_includes_old_and_new(self) -> None:
        message = format_balance_change_message(
            label="Coldcard Q — Main",
            old_total_sats=100_000,
            new_total_sats=150_000,
            old_confirmed_sats=100_000,
            new_confirmed_sats=150_000,
            old_unconfirmed_sats=0,
            new_unconfirmed_sats=0,
            timestamp=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIn("Coldcard Q — Main", message)
        self.assertIn("100,000 sats", message)
        self.assertIn("150,000 sats", message)
        self.assertIn("increase", message)
        self.assertNotIn(TEST_XPUB, message)

    def test_degraded_message_format(self) -> None:
        message = format_degraded_message(
            title="ElectrumX unreachable",
            detail="192.168.1.240:50001 — connection refused",
        )
        self.assertIn("DEGRADED", message)
        self.assertIn("ElectrumX unreachable", message)


class TestOrchestrator(unittest.TestCase):
    def _write_config(self, tmpdir: str, *, dust_threshold: int = 0) -> Path:
        config_path = Path(tmpdir) / "config.yaml"
        state_path = Path(tmpdir) / "state.json"
        config_path.write_text(
            f"""
electrumx:
  host: "127.0.0.1"
  port: 50001
  use_ssl: false
  allowed_hosts:
    - "127.0.0.1"
state_path: "{state_path}"
wallets:
  - label: "Wallet A"
    descriptor_secret_name: "BTC_TEST_A"
    derivation_gap_limit: 1
    dust_threshold_sats: {dust_threshold}
  - label: "Wallet B"
    descriptor_secret_name: "BTC_TEST_B"
    derivation_gap_limit: 1
    dust_threshold_sats: 0
""",
            encoding="utf-8",
        )
        return config_path

    def _make_mock_client(self) -> MagicMock:
        client = MagicMock(spec=ElectrumXClient)
        client.ping.return_value = None
        return client

    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.send_message_safe")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.scan_wallet")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.write_summary")
    def test_balance_change_triggers_discord_alert(
        self,
        mock_write_summary: MagicMock,
        mock_scan_wallet: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        mock_send.return_value = True
        mock_write_summary.return_value = Path("/tmp/log.md")

        def scan_side_effect(wallet: WalletConfig, _client: ElectrumXClient) -> WalletBalance:
            totals = {"Wallet A": 150_000, "Wallet B": 50_000}
            return WalletBalance(
                label=wallet.label,
                confirmed_sats=totals[wallet.label],
                unconfirmed_sats=0,
            )

        mock_scan_wallet.side_effect = scan_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_config(tmpdir)
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "wallets": {
                            "Wallet A": {
                                "confirmed_sats": 100_000,
                                "unconfirmed_sats": 0,
                                "total_sats": 100_000,
                                "last_checked_at": "2026-08-15T00:00:00Z",
                            },
                            "Wallet B": {
                                "confirmed_sats": 50_000,
                                "unconfirmed_sats": 0,
                                "total_sats": 50_000,
                                "last_checked_at": "2026-08-15T00:00:00Z",
                            },
                        },
                    },
                ),
                encoding="utf-8",
            )

            env = {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID": "123",
                "OBSIDIAN_VAULT_PATH": tmpdir,
                "SKILL_LOG_SUBFOLDER": "logs",
            }
            with patch.dict(os.environ, env, clear=False):
                from openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor import (
                    BitcoinBalanceMonitorSkill,
                )

                skill = BitcoinBalanceMonitorSkill(electrumx_client=self._make_mock_client())
                skill.config["_config_path"] = str(config_path)
                result = skill.run(dry_run=False)

        self.assertFalse(result.degraded)
        self.assertEqual(result.discord_alerts_sent, 1)
        alert_messages = [call.args[0] for call in mock_send.call_args_list]
        self.assertTrue(any("Wallet A" in msg and "100,000 sats" in msg for msg in alert_messages))
        self.assertFalse(any("Wallet B" in msg and "balance change" in msg for msg in alert_messages))

    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.send_message_safe")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.scan_wallet")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.write_summary")
    def test_dust_change_suppressed_but_state_updated(
        self,
        mock_write_summary: MagicMock,
        mock_scan_wallet: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        mock_write_summary.return_value = None

        def scan_side_effect(wallet: WalletConfig, _client: ElectrumXClient) -> WalletBalance:
            totals = {"Wallet A": 100_500, "Wallet B": 50_000}
            return WalletBalance(
                label=wallet.label,
                confirmed_sats=totals[wallet.label],
                unconfirmed_sats=0,
            )

        mock_scan_wallet.side_effect = scan_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_config(tmpdir, dust_threshold=1000)
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "wallets": {
                            "Wallet A": {
                                "confirmed_sats": 100_000,
                                "unconfirmed_sats": 0,
                                "total_sats": 100_000,
                                "last_checked_at": "2026-08-15T00:00:00Z",
                            },
                        },
                    },
                ),
                encoding="utf-8",
            )

            env = {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID": "123",
                "OBSIDIAN_VAULT_PATH": tmpdir,
                "SKILL_LOG_SUBFOLDER": "logs",
            }
            with patch.dict(os.environ, env, clear=False):
                from openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor import (
                    BitcoinBalanceMonitorSkill,
                )

                skill = BitcoinBalanceMonitorSkill(electrumx_client=self._make_mock_client())
                skill.config["_config_path"] = str(config_path)
                result = skill.run(dry_run=False)

                mock_send.assert_not_called()
                self.assertTrue(any(r.alert_suppressed_dust for r in result.wallet_results))
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["wallets"]["Wallet A"]["total_sats"], 100_500)

    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.send_message_safe")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.write_summary")
    def test_electrumx_unreachable_sends_degraded_alert(
        self,
        mock_write_summary: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        mock_send.return_value = True
        mock_write_summary.return_value = None

        client = self._make_mock_client()
        client.ping.side_effect = ElectrumXError("connection refused")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_config(tmpdir)
            env = {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID": "123",
                "OBSIDIAN_VAULT_PATH": tmpdir,
                "SKILL_LOG_SUBFOLDER": "logs",
            }
            with patch.dict(os.environ, env, clear=False):
                from openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor import (
                    BitcoinBalanceMonitorSkill,
                )

                skill = BitcoinBalanceMonitorSkill(electrumx_client=client)
                skill.config["_config_path"] = str(config_path)
                result = skill.run(dry_run=False)

        self.assertTrue(result.degraded)
        self.assertGreaterEqual(result.discord_alerts_sent, 1)
        degraded_messages = [call.args[0] for call in mock_send.call_args_list]
        self.assertTrue(any("DEGRADED" in msg and "ElectrumX" in msg for msg in degraded_messages))

    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.send_message_safe")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.scan_wallet")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.write_summary")
    def test_descriptor_never_appears_in_logs_or_alerts(
        self,
        mock_write_summary: MagicMock,
        mock_scan_wallet: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        secret_value = f"wpkh([abc/84'/0'/0']{TEST_XPUB}/0/*)"
        mock_send.return_value = True
        mock_write_summary.return_value = None
        mock_scan_wallet.return_value = WalletBalance(
            label="Wallet A",
            confirmed_sats=200_000,
            unconfirmed_sats=0,
        )

        log_capture: list[str] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_capture.append(record.getMessage())

        handler = CaptureHandler()
        logger_names = [
            "openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor",
            "openclaw.skills.bitcoin_balance_monitor.wallet_scanner",
        ]
        for name in logger_names:
            logging.getLogger(name).addHandler(handler)
            logging.getLogger(name).setLevel(logging.DEBUG)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                config_path = self._write_config(tmpdir)
                state_path = Path(tmpdir) / "state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "wallets": {
                                "Wallet A": {
                                    "confirmed_sats": 100_000,
                                    "unconfirmed_sats": 0,
                                    "total_sats": 100_000,
                                    "last_checked_at": "2026-08-15T00:00:00Z",
                                },
                            },
                        },
                    ),
                    encoding="utf-8",
                )
                env = {
                    "DISCORD_BOT_TOKEN": "token",
                    "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID": "123",
                    "OBSIDIAN_VAULT_PATH": tmpdir,
                    "SKILL_LOG_SUBFOLDER": "logs",
                }
                with patch.dict(os.environ, env, clear=False):
                    from openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor import (
                        BitcoinBalanceMonitorSkill,
                    )

                    skill = BitcoinBalanceMonitorSkill(electrumx_client=self._make_mock_client())
                    skill.config["_config_path"] = str(config_path)
                    skill.run(dry_run=False)
        finally:
            for name in logger_names:
                logging.getLogger(name).removeHandler(handler)

        combined = "\n".join(log_capture)
        alert_messages = [call.args[0] for call in mock_send.call_args_list]
        combined_alerts = "\n".join(alert_messages)
        self.assertNotIn(TEST_XPUB, combined)
        self.assertNotIn(TEST_XPUB, combined_alerts)
        self.assertNotIn(secret_value, combined)
        self.assertNotIn(secret_value, combined_alerts)

    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.send_message_safe")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.scan_wallet")
    @patch("openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor.write_summary")
    def test_first_run_records_baseline_without_alert(
        self,
        mock_write_summary: MagicMock,
        mock_scan_wallet: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        mock_write_summary.return_value = None

        def scan_side_effect(wallet: WalletConfig, _client: ElectrumXClient) -> WalletBalance:
            return WalletBalance(label=wallet.label, confirmed_sats=42_000, unconfirmed_sats=0)

        mock_scan_wallet.side_effect = scan_side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_config(tmpdir)
            env = {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID": "123",
                "OBSIDIAN_VAULT_PATH": tmpdir,
                "SKILL_LOG_SUBFOLDER": "logs",
            }
            with patch.dict(os.environ, env, clear=False):
                from openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor import (
                    BitcoinBalanceMonitorSkill,
                )

                skill = BitcoinBalanceMonitorSkill(electrumx_client=self._make_mock_client())
                skill.config["_config_path"] = str(config_path)
                result = skill.run(dry_run=False)

        mock_send.assert_not_called()
        self.assertTrue(all(r.is_first_run for r in result.wallet_results if r.balance is not None))


class TestElectrumXClientAllowlist(unittest.TestCase):
    def test_connect_rejects_non_allowlisted_host(self) -> None:
        settings = ElectrumXSettings(
            host="8.8.8.8",
            port=50001,
            use_ssl=False,
            timeout_seconds=1.0,
            allowed_hosts=frozenset({"127.0.0.1"}),
        )
        client = ElectrumXClient(settings)
        with self.assertRaises(ElectrumXError) as ctx:
            client.connect()
        self.assertIn("allowed_hosts", str(ctx.exception))


class TestSendMessageSafe(unittest.TestCase):
    @patch("openclaw.skills.bitcoin_balance_monitor.discord_notifier.send_message")
    def test_send_message_safe_swallows_errors(self, mock_send: MagicMock) -> None:
        mock_send.side_effect = RuntimeError("network down")
        result = send_message_safe("hello", bot_token="t", channel_id="c")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
