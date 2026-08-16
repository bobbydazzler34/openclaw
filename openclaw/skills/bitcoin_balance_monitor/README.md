# Bitcoin balance monitor (`bitcoin_balance_monitor`)

Watch-only monitor for Bitcoin wallets. Each run derives addresses from output descriptors or xpubs (loaded from secrets), queries balances via the **local ElectrumX node on thoth** (`192.168.1.240:50001`), compares against persisted state, and posts Discord alerts when balances change.

Runs on the Raspberry Pi **sempiternal** via a systemd timer every 15 minutes.

**Security:** No seed, xprv, or private key material is ever loaded. Descriptors/xpubs live in `/etc/openclaw/secrets.env` and are accessed only via `get_secret()`. Wallet labels (not descriptors) appear in logs and alerts.

---

## Prerequisites

- Python 3.11+
- Local ElectrumX on thoth reachable from sempiternal (`nc -zv 192.168.1.240 50001`)
- Watch-only output descriptors or xpubs exported from Sparrow / Coldcard Q
- Discord bot token and channel ID

---

## Environment variables

Secrets are read from the environment only — never put them in `config.yaml`. systemd loads `/etc/openclaw/secrets.env` before the process starts.

| Variable | Purpose |
|----------|---------|
| `BTC_WATCH_*_DESCRIPTOR` | One per wallet — output descriptor or bare xpub (see below) |
| `DISCORD_BOT_TOKEN` | Shared sempiternal bot token |
| `DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID` | Discord channel snowflake for alerts |
| `OBSIDIAN_VAULT_PATH` | Obsidian vault root (e.g. `/mnt/onedrive/obsidian/OpenClawVault`) |
| `SKILL_LOG_SUBFOLDER` | Log subfolder (e.g. `OpenClaw/Logs`) |

Access descriptor secrets in Python via `from openclaw.secrets import get_secret`.

### Supported descriptor formats

- **Output descriptor** from Sparrow/Coldcard, e.g. `wpkh([fingerprint/84'/0'/0']xpub.../0/*)#checksum`
- **Bare xpub** — inferred as `wpkh(xpub/0/*)` receive and `wpkh(xpub/1/*)` change

---

## Configuration

Edit [`config.yaml`](config.yaml):

| Key | Purpose |
|-----|---------|
| `electrumx.host` | ElectrumX host (must be in `allowed_hosts`) |
| `electrumx.port` | ElectrumX port (default `50001`) |
| `electrumx.allowed_hosts` | Hard-enforced allowlist — no public servers |
| `state_path` | JSON state file (default `/var/lib/openclaw/bitcoin_balance_monitor/state.json`) |
| `wallets[].label` | Human-readable name (safe for logs/alerts) |
| `wallets[].descriptor_secret_name` | Key in `secrets.env` |
| `wallets[].derivation_gap_limit` | Gap limit per chain (default `20`) |
| `wallets[].dust_threshold_sats` | Suppress alerts below this delta (default `0`) |

Add additional wallets by appending entries under `wallets:` — no code changes needed.

---

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x openclaw/skills/bitcoin_balance_monitor/run_bitcoin_balance_monitor.sh
```

### First-time secrets setup

```bash
sudo mkdir -p /etc/openclaw /var/lib/openclaw/bitcoin_balance_monitor
sudo touch /etc/openclaw/secrets.env
sudo chown root:openclaw /etc/openclaw/secrets.env
sudo chmod 640 /etc/openclaw/secrets.env
sudo chown -R openclaw:openclaw /var/lib/openclaw/bitcoin_balance_monitor
sudo chmod 750 /var/lib/openclaw/bitcoin_balance_monitor
sudo nano /etc/openclaw/secrets.env
```

Example `secrets.env` lines (no `export` keyword):

```
BTC_WATCH_MAIN_DESCRIPTOR=wpkh([deadbeef/84'/0'/0']xpub6C.../0/*)#checksum
DISCORD_BOT_TOKEN=...
DISCORD_BITCOIN_BALANCE_MONITOR_CHANNEL_ID=...
OBSIDIAN_VAULT_PATH=/mnt/onedrive/obsidian/OpenClawVault
SKILL_LOG_SUBFOLDER=OpenClaw/Logs
```

---

## Run manually

```bash
export PYTHONPATH=/path/to/OpenClaw
./openclaw/skills/bitcoin_balance_monitor/run_bitcoin_balance_monitor.sh
```

Dry run (scan only, no state/Discord/Obsidian):

```bash
PYTHONPATH=. python -m openclaw.skills.bitcoin_balance_monitor.bitcoin_balance_monitor --dry-run
```

First manual run records baselines without alerting.

---

## Systemd timer

```bash
sudo cp openclaw/skills/bitcoin_balance_monitor/bitcoin-balance-monitor.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bitcoin-balance-monitor.timer
sudo systemctl status bitcoin-balance-monitor.timer
```

Change interval: edit `OnUnitActiveSec` in the timer unit or `systemctl edit bitcoin-balance-monitor.timer`.

---

## Acceptance testing

1. **Balance change alert:** Edit `state.json` to set a different `total_sats` for a wallet label, then run the monitor. Expect a Discord message with old → new values.
2. **Node unreachable:** Stop ElectrumX on thoth temporarily. Expect a `BTC monitor DEGRADED — ElectrumX unreachable` Discord alert and exit code `1`.
3. **Dust suppression:** Set `dust_threshold_sats: 1000` and simulate a sub-threshold state delta. Expect no Discord alert but updated state.

---

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s openclaw/skills/bitcoin_balance_monitor/tests -p "test_*.py" -v
```

---

## Security notes

- Descriptors/xpubs must never appear in Discord messages, Obsidian logs, or application logs.
- Only allowlisted ElectrumX hosts are permitted (`192.168.1.240`, `127.0.0.1`, `localhost`).
- Read-only RPC methods only (`server.version`, `blockchain.scripthash.get_balance`).
- State file should remain mode `640`, directory `750`, owned by `openclaw`.
