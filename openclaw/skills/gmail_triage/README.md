# Gmail triage and compose (`gmail_triage`)

Automate Gmail triage (fetch, classify, reply **drafts** only) and **compose new outbound drafts** from natural language. Gmail is accessed via the **Maton** gateway; LLM is **Google Gemini**. State is stored in **Supabase**; run logs go to **Obsidian**.

**Nothing sends mail automatically** — only Gmail **drafts** are created.

---

## Prerequisites

- Maton account with an active **google-mail** connection
- Supabase project and **service role** key (not the anon/public key)
- Gemini API key (`GEMINI_API_KEY`)
- Obsidian vault path on disk (`OBSIDIAN_VAULT_PATH`, `SKILL_LOG_SUBFOLDER`)

---

## Environment variables

Load via **systemd** (`openclaw.service`) or export in the shell. The skill uses `os.environ` only (no `python-dotenv`).

| Variable | Purpose |
|----------|---------|
| `MATON_API_KEY` | Maton API bearer token |
| `MATON_BASE_URL` | e.g. `https://gateway.maton.ai` |
| `GMAIL_ACCOUNT_EMAIL` | Mailbox address (From / logging) |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_KEY` | **Service role** secret |
| `GEMINI_API_KEY` | Gemini API key |
| `OBSIDIAN_VAULT_PATH` | Vault root |
| `SKILL_LOG_SUBFOLDER` | Subfolder for logs (e.g. `OpenClaw/Logs`) |
| `OBSIDIAN_USER` | Optional short id used in **triage** run log filenames (same idea as `rba_fx`) |

After editing the unit file:

```bash
sudo systemctl daemon-reload
sudo systemctl restart openclaw.service
sudo systemctl status openclaw.service
```

---

## Supabase schema

Apply the SQL in the repo root [`supabase_schema.sql`](../../../supabase_schema.sql) once (Supabase **SQL Editor**, or `psql` with your **database** connection string — not the HTTP API URL alone).

Tables include `gmail_triage_runs`, `gmail_triage_scans`, and **`gmail_composed_drafts`** (audit log for Telegram/Discord compose).

---

## Running triage (batch)

From the repository root with `PYTHONPATH` set to the parent of the `openclaw` package:

```bash
export PYTHONPATH=/path/to/OpenClaw
set -a && source .env && set +a   # if you use a local .env file
python -m openclaw.skills.gmail_triage.skill
```

Or:

```python
from openclaw.skills.gmail_triage import GmailTriageSkill
GmailTriageSkill().run()
```

---

## Compose (Telegram / Discord)

The skill exposes **`async def run_compose(instruction, triggered_by)`** where `triggered_by` is `"telegram"` or `"discord"`.

- **`format_compose_reply(composed)`** returns the short text to post back to the user.

### Discord (`@sempiternal`)

The **sempiternal** bot runs in your OpenClaw deployment (e.g. Raspberry Pi). This repository does **not** ship the Discord bot; wire the handler in that codebase:

1. Detect bot mention **and** the word `compose` (case-insensitive).
2. Strip the mention; pass the remainder as `instruction`.
3. `result = await run_compose(instruction, triggered_by="discord")`
4. `await message.channel.send(format_compose_reply(result))`

Example prompt:

```text
@sempiternal compose email to john@example.com about the unpaid invoice from last month
```

The recipient address **must appear literally** in the instruction (the composer rejects model output that is not present in the text, to avoid hallucinated addresses).

### Telegram

Register a **`/compose`** command that passes everything after the command as `instruction`:

```text
/compose Email john@example.com about the unpaid invoice from last month
```

Use `run_compose(instruction, triggered_by="telegram")`.

### Confirmation messages

**Success (draft saved in Gmail):**

```text
✅ Draft saved
To: john@example.com
Subject: …
Check Gmail drafts to review before sending.
```

**Missing recipient:**

```text
⚠️ Could not compose draft — recipient email address missing.
Try: @sempiternal compose email to john@example.com about the invoice
```

**Failure:**

```text
❌ Draft failed — check OpenClaw logs for details.
```

### Synchronous wrapper

```python
from openclaw.skills.gmail_triage import run_compose_sync

run_compose_sync("email a@b.com about x", "telegram")
```

---

## Obsidian logs

- **Triage:** per-run file `{UTC}_gmail_triage_{tag}.md` (same stem pattern as `rba_fx`).
- **Compose:** append-only sections in a **daily** note `gmail-triage-{YYYY-MM-DD}.md` under `SKILL_LOG_SUBFOLDER`.

Obsidian write failures are logged and do **not** fail the skill.

---

## Security

- Drafts only; **no send** endpoints are used.
- Supabase stores a **200-character preview** of composed bodies, not the full body.
- Secrets only from the environment / systemd unit; restrict unit file permissions (`chmod 600`).

---

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest openclaw.skills.gmail_triage.tests.test_composer -v
```
