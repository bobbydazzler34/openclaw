"""Environment-backed secret access (loaded by systemd EnvironmentFile)."""

from __future__ import annotations

import os


def get_secret(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Required secret '{key}' not found in environment")
    return value
