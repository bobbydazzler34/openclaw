"""Configuration loading for bitcoin_balance_monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from openclaw.skills.bitcoin_balance_monitor.models import ElectrumXSettings, WalletConfig

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

DEFAULT_ALLOWED_HOSTS = frozenset({"192.168.1.240", "127.0.0.1", "localhost"})


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    """Resolved monitor configuration."""

    electrumx: ElectrumXSettings
    state_path: Path
    wallets: tuple[WalletConfig, ...]
    obsidian_vault_path: str
    skill_log_subfolder: str
    discord_bot_token: str
    discord_channel_id: str


def _normalize_host(host: str) -> str:
    return host.strip().lower()


def _load_yaml(config_path: Path) -> dict:
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        msg = f"Expected mapping in config file: {config_path}"
        raise ValueError(msg)
    return data


def _parse_electrumx(raw: dict) -> ElectrumXSettings:
    section = raw.get("electrumx") or {}
    if not isinstance(section, dict):
        raise ValueError("config electrumx must be a mapping")

    host = str(section.get("host", "192.168.1.240")).strip()
    port = int(section.get("port", 50001))
    use_ssl = bool(section.get("use_ssl", False))
    timeout_seconds = float(section.get("timeout_seconds", 30))

    allowed_raw = section.get("allowed_hosts") or list(DEFAULT_ALLOWED_HOSTS)
    if not isinstance(allowed_raw, list):
        raise ValueError("electrumx.allowed_hosts must be a list")
    allowed_hosts = frozenset(_normalize_host(str(item)) for item in allowed_raw if str(item).strip())

    normalized_host = _normalize_host(host)
    if normalized_host not in allowed_hosts:
        msg = (
            f"ElectrumX host {host!r} is not in allowed_hosts {sorted(allowed_hosts)}. "
            "Refusing to connect to non-allowlisted hosts."
        )
        raise ValueError(msg)

    if use_ssl:
        raise ValueError("SSL ElectrumX is not supported; use plain TCP (use_ssl: false)")

    return ElectrumXSettings(
        host=host,
        port=port,
        use_ssl=use_ssl,
        timeout_seconds=timeout_seconds,
        allowed_hosts=allowed_hosts,
    )


def _parse_wallets(raw: dict) -> tuple[WalletConfig, ...]:
    wallets_raw = raw.get("wallets") or []
    if not isinstance(wallets_raw, list):
        raise ValueError("config wallets must be a list")
    if not wallets_raw:
        raise ValueError("config wallets must contain at least one wallet")

    wallets: list[WalletConfig] = []
    seen_labels: set[str] = set()
    for index, item in enumerate(wallets_raw):
        if not isinstance(item, dict):
            raise ValueError(f"wallets[{index}] must be a mapping")

        label = str(item.get("label", "")).strip()
        secret_name = str(item.get("descriptor_secret_name", "")).strip()
        if not label:
            raise ValueError(f"wallets[{index}] missing label")
        if not secret_name:
            raise ValueError(f"wallets[{index}] missing descriptor_secret_name")
        if label in seen_labels:
            raise ValueError(f"duplicate wallet label: {label}")
        seen_labels.add(label)

        gap_limit = int(item.get("derivation_gap_limit", 20))
        if gap_limit < 1:
            raise ValueError(f"wallets[{index}] derivation_gap_limit must be >= 1")

        dust_threshold = int(item.get("dust_threshold_sats", 0))
        if dust_threshold < 0:
            raise ValueError(f"wallets[{index}] dust_threshold_sats must be >= 0")

        wallets.append(
            WalletConfig(
                label=label,
                descriptor_secret_name=secret_name,
                derivation_gap_limit=gap_limit,
                dust_threshold_sats=dust_threshold,
            ),
        )

    return tuple(wallets)


def load_config(
    config_path: Path | str | None = None,
    *,
    require_discord: bool = True,
    require_obsidian: bool = True,
) -> MonitorConfig:
    """Load YAML config and validate environment variables."""
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    raw = _load_yaml(path)

    state_path = Path(str(raw.get("state_path", "/var/lib/openclaw/bitcoin_balance_monitor/state.json")))

    required_env: list[str] = []
    if require_discord:
        required_env.extend(["DISCORD_BOT_TOKEN", "DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID"])
    if require_obsidian:
        required_env.extend(["OBSIDIAN_VAULT_PATH", "SKILL_LOG_SUBFOLDER"])

    missing = [name for name in required_env if not (os.environ.get(name) or "").strip()]
    if missing:
        lines = "\n".join(f"  - {name}" for name in missing)
        msg = f"Missing or empty required environment variables:\n{lines}"
        raise OSError(msg)

    discord_token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    discord_channel = (os.environ.get("DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID") or "").strip()
    vault_path = (os.environ.get("OBSIDIAN_VAULT_PATH") or "").strip()
    log_subfolder = (os.environ.get("SKILL_LOG_SUBFOLDER") or "").strip()

    return MonitorConfig(
        electrumx=_parse_electrumx(raw),
        state_path=state_path,
        wallets=_parse_wallets(raw),
        obsidian_vault_path=vault_path,
        skill_log_subfolder=log_subfolder,
        discord_bot_token=discord_token,
        discord_channel_id=discord_channel,
    )
