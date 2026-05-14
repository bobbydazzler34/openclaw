"""Unit tests for Gmail compose flow (no send endpoints)."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openclaw.skills.gmail_triage.models import ComposedEmail
from openclaw.skills.gmail_triage.skill import format_compose_reply, run_compose


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        gemini_api_key="fake",
        gmail_account_email="me@example.com",
        maton_base_url="https://gateway.maton.ai",
        maton_api_key="fake-maton",
        supabase_url="https://x.supabase.co",
        supabase_service_key="fake-sb",
        obsidian_vault_path="/tmp/openclaw_obs_test",
        skill_log_subfolder="logs",
    )


class RunComposeTests(unittest.TestCase):
    """Exercise ``run_compose`` with mocked IO."""

    def test_missing_recipient_skips_gmail_draft(self) -> None:
        """When composer yields no recipient, Gmail create_new_draft must not run."""

        async def _body() -> None:
            missing = ComposedEmail(
                to=None,
                subject="Hi",
                body="Body",
                instruction="say hello",
                composed_at=datetime.now(timezone.utc),
                draft_id=None,
                status="missing_recipient",
            )
            mock_gm = MagicMock()
            mock_gm.create_new_draft = AsyncMock(return_value="should-not-run")
            mock_store = MagicMock()

            with patch("openclaw.skills.gmail_triage.skill.load_config", return_value=_cfg()):
                with patch("openclaw.skills.gmail_triage.skill.compose_email", new=AsyncMock(return_value=missing)):
                    with patch("openclaw.skills.gmail_triage.skill.write_compose_log"):
                        out = await run_compose(
                            "say hello without any email",
                            "discord",
                            store=mock_store,
                            gmail_client=mock_gm,
                        )
            self.assertEqual(out.status, "missing_recipient")
            mock_gm.create_new_draft.assert_not_called()
            mock_store.insert_composed_draft.assert_called_once()

        asyncio.run(_body())

    def test_success_calls_create_new_draft_only(self) -> None:
        """Happy path calls Maton draft create once; no send URL involved."""

        async def _body() -> None:
            ok = ComposedEmail(
                to="john@example.com",
                subject="Invoice",
                body="Please pay.",
                instruction="email john@example.com about invoice",
                composed_at=datetime.now(timezone.utc),
                draft_id=None,
                status="drafted",
            )
            mock_gm = MagicMock()
            mock_gm.create_new_draft = AsyncMock(return_value="draft-id-123")
            mock_store = MagicMock()

            with patch("openclaw.skills.gmail_triage.skill.load_config", return_value=_cfg()):
                with patch("openclaw.skills.gmail_triage.skill.compose_email", new=AsyncMock(return_value=ok)):
                    with patch("openclaw.skills.gmail_triage.skill.write_compose_log"):
                        out = await run_compose(
                            "email john@example.com about invoice",
                            "telegram",
                            store=mock_store,
                            gmail_client=mock_gm,
                        )
            self.assertEqual(out.status, "drafted")
            self.assertEqual(out.draft_id, "draft-id-123")
            mock_gm.create_new_draft.assert_called_once()
            call_kw = mock_gm.create_new_draft.call_args
            self.assertEqual(call_kw.kwargs, {})
            args = call_kw[0]
            self.assertEqual(args[0], "me@example.com")
            self.assertEqual(args[1], "john@example.com")
            self.assertEqual(args[2], "Invoice")
            self.assertEqual(args[3], "Please pay.")
            mock_store.insert_composed_draft.assert_called_once()

        asyncio.run(_body())

    def test_format_compose_reply_messages(self) -> None:
        ok = ComposedEmail(
            to="a@b.co",
            subject="S",
            body="B",
            instruction="i",
            composed_at=datetime.now(timezone.utc),
            draft_id="d1",
            status="drafted",
        )
        self.assertIn("Draft saved", format_compose_reply(ok))
        miss = ok.model_copy(update={"status": "missing_recipient", "draft_id": None, "to": None})
        self.assertIn("recipient", format_compose_reply(miss).lower())
        fail = ok.model_copy(update={"status": "failed", "draft_id": None})
        self.assertIn("failed", format_compose_reply(fail).lower())


if __name__ == "__main__":
    unittest.main()
