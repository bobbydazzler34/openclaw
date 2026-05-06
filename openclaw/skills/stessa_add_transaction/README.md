# stessa_add_transaction

Log a transaction in [Stessa](https://www.stessa.com/) from a short natural-language instruction (e.g. from Telegram). The skill:

1. Calls **Google Gemini** (`gemini-2.5-flash` by default) to extract structured fields as JSON.
2. Validates **amount**, **date**, **category**, **subcategory**, and **property** against the built-in category map.
3. Runs **Playwright** (sync API in a thread pool via `asyncio.run_in_executor`) to sign in and submit the transaction.

**Sign-in:** Stessa may redirect to **Roofstock** hosted login at `auth.roofstock.com` (Auth0-style). The skill looks for email/password in the main page and in **iframes**, and can step through a **Continue**-style flow before the password field. If login breaks after a Stessa or IdP update, adjust the TODO-marked selectors in `stessa_add_transaction.py` under `_stessa_roofstock_login`.

## Requirements

Install OS dependencies on the machine that runs the skill:

```bash
pip install -r requirements.txt
playwright install chromium
```

Set the Gemini API key (name is configurable in `config.yaml`, default `GOOGLE_API_KEY`):

```bash
export GOOGLE_API_KEY="your-key"
```

## Configuration

Edit [`config.yaml`](config.yaml):

| Key | Purpose |
|-----|---------|
| `stessa_username` | Stessa login email |
| `stessa_password` | Stessa login password |
| `gemini_api_key_env` | Environment variable name holding the Gemini API key |
| `llm_model` | Gemini model id (default `gemini-2.5-flash`) |
| `playwright_cdp_url` | Optional; if set, connect to existing Chromium via CDP instead of launching headless |
| `require_cdp_session` | If `true`, do not launch headless browser; require `playwright_cdp_url` |
| `cdp_require_authenticated_session` | If `true`, fail fast when attached CDP browser is not already signed in |
| `allow_login_fallback` | If `true`, allow scripted login even when attached CDP session is not authenticated |

Passwords in YAML are convenient on a private host (e.g. a Raspberry Pi) but are sensitive—restrict file permissions and avoid committing real secrets.

### Unattended Telegram mode (recommended)

Use CDP with a persistent, already-authenticated browser session:

1. Start Chromium with remote debugging (example):
   `chromium --remote-debugging-port=9222`
2. Set `playwright_cdp_url: http://127.0.0.1:9222`.
3. Keep:
   - `require_cdp_session: true`
   - `cdp_require_authenticated_session: true`
   - `allow_login_fallback: false`
4. Complete login + human verification once in that browser; then Telegram runs can proceed without interactive login prompts.

## Run

From the repo root with `PYTHONPATH` including the project (or install the package):

```bash
PYTHONPATH=. python3 -c "from openclaw.skills.stessa_add_transaction.stessa_add_transaction import run; print(run('Add \$1200 rent for ABC on 2025-05-01'))"
```

Dry demonstration (mocks Gemini and Playwright; no API or browser):

```bash
PYTHONPATH=. python3 -m openclaw.skills.stessa_add_transaction.stessa_add_transaction
```

## UI selectors

Stessa’s web app can change. The Playwright steps use role- and text-based locators with **TODO** comments in [`stessa_add_transaction.py`](stessa_add_transaction.py). Update those selectors if flows break after a Stessa update.

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s openclaw/skills/stessa_add_transaction/tests -p "test_*.py" -v
```
