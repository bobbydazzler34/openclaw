"""Supabase-backed deduplication store for seen job listings."""

from __future__ import annotations

import logging
import os
from typing import Any

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_client: Client | None = None
CHUNK_SIZE = 100


def _get_client() -> Client:
    """Return a lazily initialized Supabase client."""
    global _client  # noqa: PLW0603 — module-level singleton
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        msg = "SUPABASE_URL and SUPABASE_KEY environment variables are required"
        raise RuntimeError(msg)

    _client = create_client(url, key)
    return _client


def filter_new(jobs: list[dict]) -> list[dict]:
    """Return only jobs whose job_id is not already in the seen_jobs table."""
    if not jobs:
        return []

    client = _get_client()
    job_ids = [job["job_id"] for job in jobs]
    seen: set[str] = set()

    for i in range(0, len(job_ids), CHUNK_SIZE):
        chunk = job_ids[i : i + CHUNK_SIZE]
        try:
            resp = (
                client.table("seen_jobs")
                .select("job_id")
                .in_("job_id", chunk)
                .execute()
            )
        except Exception:
            logger.exception("Supabase query failed while filtering new jobs")
            raise

        for row in resp.data or []:
            seen.add(row["job_id"])

    return [job for job in jobs if job["job_id"] not in seen]


def mark_seen(jobs: list[dict]) -> None:
    """Upsert all jobs into seen_jobs; existing job_ids are left unchanged."""
    if not jobs:
        return

    client = _get_client()
    rows: list[dict[str, Any]] = [
        {
            "job_id": job["job_id"],
            "source": job.get("source", ""),
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("url"),
        }
        for job in jobs
    ]

    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        try:
            client.table("seen_jobs").upsert(
                chunk,
                on_conflict="job_id",
                ignore_duplicates=True,
            ).execute()
        except Exception:
            logger.exception("Supabase upsert failed while marking jobs seen")
            raise

    logger.info("Marked %d job(s) as seen in Supabase", len(jobs))
