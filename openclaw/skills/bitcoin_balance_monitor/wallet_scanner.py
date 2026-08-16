"""Watch-only wallet scanning via output descriptors / xpubs."""

from __future__ import annotations

import logging
import re

from embit.descriptor import Descriptor

from openclaw.secrets import get_secret
from openclaw.skills.bitcoin_balance_monitor.electrumx_client import ElectrumXClient, ScriptBalance
from openclaw.skills.bitcoin_balance_monitor.models import WalletBalance, WalletConfig

logger = logging.getLogger(__name__)

XPUB_PATTERN = re.compile(r"^(xpub|tpub|ypub|Ypub|zpub|Zpub)[1-9A-HJ-NP-Za-km-z]+$")
DESCRIPTOR_CHECKSUM_PATTERN = re.compile(r"#[a-z0-9]{8}$", re.IGNORECASE)


class WalletScanError(Exception):
    """Descriptor parsing or balance scan failure for one wallet."""


def _strip_descriptor_checksum(value: str) -> str:
    return DESCRIPTOR_CHECKSUM_PATTERN.sub("", value.strip())


def _normalize_watch_only_material(raw: str) -> tuple[str, str]:
    """Return (receive_descriptor, change_descriptor) without exposing secrets in logs."""
    cleaned = _strip_descriptor_checksum(raw.strip())
    if not cleaned:
        raise WalletScanError("Descriptor secret is empty")

    if XPUB_PATTERN.match(cleaned):
        receive = f"wpkh({cleaned}/0/*)"
        change = f"wpkh({cleaned}/1/*)"
        return receive, change

    if "/*" not in cleaned and "/<0;1>/*" not in cleaned:
        raise WalletScanError(
            "Descriptor must be a ranged output descriptor (contain /*) or a bare xpub",
        )

    receive = cleaned
    if "/0/*" in cleaned:
        change = cleaned.replace("/0/*", "/1/*", 1)
    elif "/<0;1>/*" in cleaned:
        change = cleaned.replace("/<0;1>/*", "/1/*", 1)
        receive = cleaned.replace("/<0;1>/*", "/0/*", 1)
    else:
        change = cleaned

    return receive, change


def _parse_descriptor(descriptor_str: str) -> Descriptor:
    try:
        return Descriptor.from_string(descriptor_str)
    except Exception as exc:  # noqa: BLE001
        raise WalletScanError(f"Invalid output descriptor: {exc}") from exc


def _scan_chain(
    client: ElectrumXClient,
    descriptor: Descriptor,
    *,
    gap_limit: int,
) -> ScriptBalance:
    """Scan one chain using the standard gap-limit algorithm."""
    total_confirmed = 0
    total_unconfirmed = 0
    consecutive_empty = 0
    index = 0

    while consecutive_empty < gap_limit:
        try:
            derived = descriptor.derive(index)
            script_pubkey = derived.script_pubkey()
        except Exception as exc:  # noqa: BLE001
            raise WalletScanError(f"Derivation failed at index {index}: {exc}") from exc

        balance = client.get_balance(script_pubkey)
        if balance.total_sats > 0:
            total_confirmed += balance.confirmed_sats
            total_unconfirmed += balance.unconfirmed_sats
            consecutive_empty = 0
        else:
            consecutive_empty += 1

        index += 1

    return ScriptBalance(
        confirmed_sats=total_confirmed,
        unconfirmed_sats=total_unconfirmed,
    )


def scan_wallet(
    wallet: WalletConfig,
    client: ElectrumXClient,
) -> WalletBalance:
    """Scan receive and change chains for a watch-only wallet."""
    raw_material = get_secret(wallet.descriptor_secret_name)
    receive_desc_str, change_desc_str = _normalize_watch_only_material(raw_material)

    receive_desc = _parse_descriptor(receive_desc_str)
    change_desc = _parse_descriptor(change_desc_str)

    logger.info("Scanning wallet %r (gap limit %d)", wallet.label, wallet.derivation_gap_limit)

    receive_balance = _scan_chain(
        client,
        receive_desc,
        gap_limit=wallet.derivation_gap_limit,
    )
    change_balance = _scan_chain(
        client,
        change_desc,
        gap_limit=wallet.derivation_gap_limit,
    )

    confirmed = receive_balance.confirmed_sats + change_balance.confirmed_sats
    unconfirmed = receive_balance.unconfirmed_sats + change_balance.unconfirmed_sats

    logger.info(
        "Wallet %r balance: %d sats confirmed, %d sats unconfirmed",
        wallet.label,
        confirmed,
        unconfirmed,
    )

    return WalletBalance(
        label=wallet.label,
        confirmed_sats=confirmed,
        unconfirmed_sats=unconfirmed,
    )
