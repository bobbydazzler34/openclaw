# farefynder_monitor

Synthetic authentication monitor for the FareFynder Supabase project. Every run signs in with a dedicated test account (email/password, anon key), refreshes the JWT, records pass/fail and latency to `monitoring.check_results`, and sends a Telegram alert on failure only.

Runs on the Raspberry Pi **sempiternal** via a systemd timer every 5 minutes.

---

## Prerequisites

- Python 3.11+
- FareFynder Supabase project with Auth enabled
- Dedicated monitor Auth user (not a real driver account)
- `monitoring` schema exposed in Supabase API settings (Dashboard → Settings → API → Exposed schemas)
- Network access from the host (Raspberry Pi)
- Telegram bot token and chat ID (shared with other OpenClaw Pi services)

---

## Environment variables

Secrets are read from the environment only — never put them in `config.yaml`. systemd loads `/etc/openclaw/secrets.env` before the process starts.

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | FareFynder Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (writes to `monitoring.check_results`) |
| `SUPABASE_ANON_KEY` | Anon/public key (Auth sign-in and refresh, same as iOS app) |
| `FAREFYNDER_MONITOR_EMAIL` | Dedicated monitor account email |
| `FAREFYNDER_MONITOR_PASSWORD` | Dedicated monitor account password |
| `TELEGRAM_BOT_TOKEN` | Existing OpenClaw Telegram bot token |
| `TELEGRAM_CHAT_ID` | Chat ID for failure alerts |

Access secrets in Python via `from openclaw.secrets import get_secret`.

---

## Configuration

Edit [`config.yaml`](config.yaml):

| Key | Purpose |
|-----|---------|
| `check_name` | Row identifier in `check_results` (default `synthetic_login`) |
| `http_timeout_seconds` | Per-request HTTP timeout (default `10`) |

---

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Run manually

From the repository root with secrets exported (or sourced from your env file in a dev shell — do not commit secrets):

```bash
export PYTHONPATH=/path/to/OpenClaw
# export SUPABASE_URL=... etc.

python -m openclaw.skills.farefynder_monitor.farefynder_monitor
```

Exit code `0` when the orchestrator finishes (including recorded check failures). Check stderr logs and `monitoring.check_results` for outcomes.

---

### First-time secrets setup

```bash
sudo mkdir -p /etc/openclaw
sudo touch /etc/openclaw/secrets.env
sudo chown root:openclaw /etc/openclaw/secrets.env
sudo chmod 640 /etc/openclaw/secrets.env
sudo nano /etc/openclaw/secrets.env
```

Populate with these key=value pairs (no `export` keyword):

```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
SUPABASE_ANON_KEY=eyJ...
FAREFYNDER_MONITOR_EMAIL=monitor@farefynder.com
FAREFYNDER_MONITOR_PASSWORD=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Create the test account

1. Open the FareFynder Supabase project dashboard.
2. Go to **Authentication → Users → Invite user** (or add user with email/password).
3. Use an email such as `monitor@farefynder.com` and set a strong password; store it in `FAREFYNDER_MONITOR_PASSWORD`.
4. This account must **never** hold driver data or a subscription entitlement — it exists only for synthetic login checks.
5. Ensure email/password sign-in is enabled and MFA is not required for this user.

### Run the migration

1. Open the Supabase **SQL Editor**.
2. Run the contents of [`migrations/001_monitoring_check_results.sql`](migrations/001_monitoring_check_results.sql) to create the `monitoring` schema and `check_results` table.
3. In **Settings → API → Exposed schemas**, add `monitoring` if it is not already listed.

### Enable and start the timer

Copy unit files to the Pi, then enable the timer:

```bash
sudo cp openclaw/skills/farefynder_monitor/farefynder-monitor.service \
        openclaw/skills/farefynder_monitor/farefynder-monitor.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now farefynder-monitor.timer
```

### Verify it is working

```bash
# Check timer schedule
systemctl status farefynder-monitor.timer

# Watch the next run live
journalctl -u farefynder-monitor.service -f

# Confirm a result was written to Supabase
# Run in Supabase SQL editor:
SELECT * FROM monitoring.check_results ORDER BY checked_at DESC LIMIT 5;
```

---

## Architecture

```
farefynder-monitor.timer (every 5 min)
  └── farefynder-monitor.service (oneshot)
        └── farefynder_monitor.py
              ├── checker.py     → Supabase Auth (password + refresh, anon key)
              └── reporter.py    → check_results insert (service role)
                                 → Telegram alert on fail only
```

HTTP is implemented with **httpx** only (no `supabase-py`). Auth calls match the iOS app REST paths.

---

## Security notes

- Do not log secrets or write them to `error_detail` or `raw_response` (tokens are redacted before persistence).
- Use the service role key only on the Pi host; the monitor account must not access production driver data.
- `/etc/openclaw/secrets.env` should be mode `640`, owned `root:openclaw`.
