# openclaw

`openclaw` is a Python 3.11+ project for building and running reusable automation skills.

## Skills

Skills are self-contained modules under `openclaw/skills/`. Each skill directory contains:

- `skill.py` for the skill implementation
- `config.yaml` for skill-specific configuration
- `tests/` for unit-level test coverage

Shared behavior lives in `openclaw/skills/_base/skill_base.py`, which provides common logging and config-loading utilities.

## Configuration

Global non-secret settings live in `openclaw/config/settings.yaml`. Use this file for values such as:

- `model`
- `timeout_seconds`
- `log_level`

Environment variables belong in a local `.env` file that is not committed. Start by copying values from `.env.example` and replacing placeholders with real values in your environment.
