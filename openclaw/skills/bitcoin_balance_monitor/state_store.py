"""Persist last-known wallet balances."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from openclaw.skills.bitcoin_balance_monitor.models import StoredWalletBalance, WalletBalance

logger = logging.getLogger(__name__)


class StateStore:
    """JSON-backed balance state for monitored wallets."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, StoredWalletBalance]:
        if not self._path.exists():
            return {}

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Failed to read state from %s", self._path)
            raise RuntimeError(f"Cannot read state file {self._path}: {exc}") from exc

        wallets_raw = raw.get("wallets") if isinstance(raw, dict) else None
        if not isinstance(wallets_raw, dict):
            return {}

        result: dict[str, StoredWalletBalance] = {}
        for label, entry in wallets_raw.items():
            if not isinstance(entry, dict):
                continue
            result[str(label)] = StoredWalletBalance(
                confirmed_sats=int(entry.get("confirmed_sats", 0)),
                unconfirmed_sats=int(entry.get("unconfirmed_sats", 0)),
                total_sats=int(entry.get("total_sats", 0)),
                last_checked_at=str(entry.get("last_checked_at", "")),
            )
        return result

    def save_wallet(self, balance: WalletBalance, *, checked_at: datetime | None = None) -> None:
        """Update one wallet entry and persist the full state file."""
        state = self.load()
        ts = (checked_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state[balance.label] = StoredWalletBalance(
            confirmed_sats=balance.confirmed_sats,
            unconfirmed_sats=balance.unconfirmed_sats,
            total_sats=balance.total_sats,
            last_checked_at=ts,
        )
        self._write(state)

    def _write(self, state: dict[str, StoredWalletBalance]) -> None:
        payload = {
            "wallets": {
                label: {
                    "confirmed_sats": entry.confirmed_sats,
                    "unconfirmed_sats": entry.unconfirmed_sats,
                    "total_sats": entry.total_sats,
                    "last_checked_at": entry.last_checked_at,
                }
                for label, entry in state.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self._path)
        logger.debug("Wrote state to %s", self._path)
