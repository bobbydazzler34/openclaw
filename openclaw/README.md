# openclaw

`openclaw` is a Python 3.11+ project for building and running reusable automation skills.

## Skills

Skills are self-contained modules under `openclaw/skills/`. Each skill directory contains:

- `skill.py` (or a named entry module such as `stessa_add_transaction.py`) for the skill implementation
- `config.yaml` for skill-specific configuration
- `README.md` for how to run and configure the skill
- `tests/` for unit-level test coverage

Per-skill guides: [rba_fx](skills/rba_fx/README.md), [msty_tracker](skills/msty_tracker/README.md), [sharesight_sync](skills/sharesight_sync/README.md), [sharesight_trades](skills/sharesight_trades/README.md), [stessa_add_transaction](skills/stessa_add_transaction/README.md), [job_search_suhani](skills/job_search_suhani/README.md).

Shared behavior lives in `openclaw/skills/_base/skill_base.py`, which provides common logging and config-loading utilities.

## Configuration

Global non-secret settings live in `openclaw/config/settings.yaml`. Use this file for values such as:

- `model`
- `timeout_seconds`
- `log_level`

Environment variables belong in a local `.env` file that is not committed. Start by copying values from `.env.example` and replacing placeholders with real values in your environment.
