"""Stessa transaction logging skill (natural language + Playwright)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["CATEGORIES", "ParsedTransaction", "load_skill_config", "run"]

if TYPE_CHECKING:
    from openclaw.skills.stessa_add_transaction.stessa_add_transaction import (
        CATEGORIES,
        ParsedTransaction,
        load_skill_config,
        run,
    )


def __getattr__(name: str) -> Any:
    if name in __all__:
        from openclaw.skills.stessa_add_transaction import stessa_add_transaction as _impl

        return getattr(_impl, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
