"""Gmail triage skill — Maton Gmail, Gemini, Supabase, Obsidian log."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Literal

from openclaw.skills._base.skill_base import SkillBase
from openclaw.skills.gmail_triage.classifier import classify_email
from openclaw.skills.gmail_triage.composer import compose_email
from openclaw.skills.gmail_triage.config import load_config
from openclaw.skills.gmail_triage.drafter import create_draft_response
from openclaw.skills.gmail_triage.gmail_client import MatonGmailClient
from openclaw.skills.gmail_triage.models import ComposedEmail, EmailTriageLogEntry, TriageRunSummary
from openclaw.skills.gmail_triage.obsidian_logger import write_compose_log, write_summary
from openclaw.skills.gmail_triage.supabase_client import GmailTriageStore

logger = logging.getLogger(__name__)


def format_compose_reply(composed: ComposedEmail) -> str:
    """Format a short user-facing message for Telegram or Discord."""
    if composed.status == "drafted" and composed.draft_id:
        return (
            "✅ Draft saved\n"
            f"To: {composed.to}\n"
            f"Subject: {composed.subject}\n"
            "Check Gmail drafts to review before sending."
        )
    if composed.status == "missing_recipient":
        return (
            "⚠️ Could not compose draft — recipient email address missing.\n"
            "Try: @sempiternal compose email to john@example.com about the invoice"
        )
    return "❌ Draft failed — check OpenClaw logs for details."


def _composed_supabase_row(
    account: str,
    composed: ComposedEmail,
    triggered_by: Literal["telegram", "discord"],
) -> dict[str, Any]:
    return {
        "account": account,
        "instruction": composed.instruction,
        "to_address": composed.to,
        "subject": composed.subject,
        "body_preview": (composed.body or "")[:200],
        "draft_id": composed.draft_id,
        "triggered_by": triggered_by,
        "status": composed.status,
    }


async def run_compose(
    instruction: str,
    triggered_by: Literal["telegram", "discord"],
    *,
    store: GmailTriageStore | None = None,
    gmail_client: MatonGmailClient | None = None,
) -> ComposedEmail:
    """Compose a new outbound Gmail draft from natural language (never sends)."""
    cfg = load_config()
    st = store or GmailTriageStore(cfg.supabase_url, cfg.supabase_service_key)
    gm = gmail_client or MatonGmailClient(
        base_url=cfg.maton_base_url,
        api_key=cfg.maton_api_key,
        account_email=cfg.gmail_account_email,
    )

    composed = await compose_email(instruction, None, api_key=cfg.gemini_api_key)

    def persist_and_log(c: ComposedEmail) -> None:
        st.insert_composed_draft(_composed_supabase_row(cfg.gmail_account_email, c, triggered_by))
        write_compose_log(
            c,
            triggered_by,
            account_email=cfg.gmail_account_email,
            vault_path=cfg.obsidian_vault_path,
            log_subfolder=cfg.skill_log_subfolder,
        )

    if composed.status == "failed":
        await asyncio.to_thread(persist_and_log, composed)
        return composed

    if composed.status == "missing_recipient" or composed.to is None:
        final = composed.model_copy(update={"status": "missing_recipient", "to": None, "draft_id": None})
        await asyncio.to_thread(persist_and_log, final)
        return final

    try:
        draft_id = await gm.create_new_draft(
            cfg.gmail_account_email,
            composed.to,
            composed.subject,
            composed.body,
        )
        final = composed.model_copy(update={"draft_id": draft_id, "status": "drafted"})
    except Exception as exc:  # noqa: BLE001
        logger.error("create_new_draft failed: %s", exc, exc_info=True)
        final = composed.model_copy(update={"draft_id": None, "status": "failed"})

    await asyncio.to_thread(persist_and_log, final)
    return final


def run_compose_sync(
    instruction: str,
    triggered_by: Literal["telegram", "discord"],
) -> ComposedEmail:
    """Synchronous wrapper for non-async callers (e.g. quick scripts)."""
    return asyncio.run(run_compose(instruction, triggered_by))


class GmailTriageSkill(SkillBase):
    """Fetch recent Gmail, classify, draft replies for important mail, log to Supabase/Obsidian."""

    def __init__(
        self,
        config_path: str | None = None,
        *,
        store: GmailTriageStore | None = None,
        gmail_client: MatonGmailClient | None = None,
    ) -> None:
        """Initialize the skill (secrets from environment only).

        Args:
            config_path: Optional YAML path (unused; env drives configuration).
            store: Optional Supabase store for tests.
            gmail_client: Optional Maton Gmail client for tests.
        """
        super().__init__(config_path)
        self._store = store
        self._gmail = gmail_client

    def run(self) -> TriageRunSummary:
        """Execute triage synchronously (starts an asyncio event loop)."""
        return asyncio.run(self._run_async())

    async def _run_async(self) -> TriageRunSummary:
        """Async implementation: fetch, classify, draft, persist, log."""
        cfg = load_config()
        started = datetime.now(timezone.utc)
        emails_scanned = 0
        drafts_created = 0
        flagged_delete = 0
        errors: list[str] = []
        important_entries: list[EmailTriageLogEntry] = []
        deletable_entries: list[EmailTriageLogEntry] = []
        success = True
        run_id = ""

        store = self._store or GmailTriageStore(cfg.supabase_url, cfg.supabase_service_key)
        gmail = self._gmail or MatonGmailClient(
            base_url=cfg.maton_base_url,
            api_key=cfg.maton_api_key,
            account_email=cfg.gmail_account_email,
        )

        try:
            run_id = await asyncio.to_thread(store.insert_run_running, cfg.gmail_account_email)
            emails = await gmail.fetch_recent_emails(24)

            for email in emails:
                exists = await asyncio.to_thread(store.scan_exists, email.id)
                if exists:
                    logger.info("Skip already-scanned message id=%s", email.id)
                    continue

                classification = await asyncio.to_thread(
                    classify_email,
                    email,
                    api_key=cfg.gemini_api_key,
                )
                draft_created_flag = False
                if classification.classification == "important":
                    body = await asyncio.to_thread(
                        create_draft_response,
                        email,
                        api_key=cfg.gemini_api_key,
                    )
                    if body:
                        try:
                            await gmail.create_draft(
                                cfg.gmail_account_email,
                                email.id,
                                email.subject,
                                body,
                                thread_id=email.thread_id or None,
                                reply_to_sender=email.sender,
                                rfc_message_id=email.rfc_message_id,
                            )
                            draft_created_flag = True
                            drafts_created += 1
                        except Exception as exc:  # noqa: BLE001
                            err = f"Draft failed for {email.id}: {type(exc).__name__}: {exc}"
                            logger.warning("%s", err)
                            errors.append(err)
                    else:
                        msg = f"Drafter returned empty body for {email.id}"
                        logger.info(msg)
                        errors.append(msg)

                is_delete_flag = classification.classification == "deletable"
                if is_delete_flag:
                    flagged_delete += 1

                scan_row: dict[str, Any] = {
                    "email_id": email.id,
                    "account": cfg.gmail_account_email,
                    "subject": email.subject[:5000] if email.subject else None,
                    "sender": email.sender[:2000] if email.sender else None,
                    "received_at": email.received_at.isoformat(),
                    "classification": classification.classification,
                    "draft_created": draft_created_flag,
                    "flagged_delete": is_delete_flag,
                    "run_id": run_id,
                }
                await asyncio.to_thread(store.upsert_scan, scan_row)
                emails_scanned += 1

                if classification.classification == "important":
                    important_entries.append(
                        EmailTriageLogEntry(
                            subject=email.subject or "(no subject)",
                            sender=email.sender or "",
                            received_at=email.received_at,
                            reason=classification.reason,
                        ),
                    )
                if classification.classification == "deletable":
                    deletable_entries.append(
                        EmailTriageLogEntry(
                            subject=email.subject or "(no subject)",
                            sender=email.sender or "",
                            received_at=email.received_at,
                            reason=classification.reason,
                        ),
                    )

            await asyncio.to_thread(
                store.update_run_success,
                run_id,
                emails_scanned=emails_scanned,
                drafts_created=drafts_created,
                flagged_delete=flagged_delete,
            )
        except Exception as exc:  # noqa: BLE001
            success = False
            tb = traceback.format_exc()
            err_msg = f"{type(exc).__name__}: {exc}"
            errors.append(err_msg)
            logger.error("gmail_triage failed: %s\n%s", err_msg, tb)
            if run_id:
                await asyncio.to_thread(store.update_run_failed, run_id, err_msg)

        completed = datetime.now(timezone.utc)
        summary = TriageRunSummary(
            run_id=run_id if run_id else "unknown",
            account=cfg.gmail_account_email,
            started_at=started,
            completed_at=completed,
            emails_scanned=emails_scanned,
            drafts_created=drafts_created,
            flagged_for_deletion=flagged_delete,
            errors=errors,
            success=success,
            important_entries=important_entries,
            deletable_entries=deletable_entries,
        )

        write_summary(
            summary,
            vault_path=cfg.obsidian_vault_path,
            log_subfolder=cfg.skill_log_subfolder,
        )
        return summary


def run() -> TriageRunSummary:
    """Module entrypoint for OpenClaw / scripts."""
    return GmailTriageSkill().run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run()
    print(result.model_dump_json(indent=2))
