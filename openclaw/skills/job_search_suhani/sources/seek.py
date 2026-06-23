"""Seek public job search client.

Uses the unauthenticated frontend API that powers seek.com.au search results.
This is NOT an officially documented API — response shape may change without notice.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

SEEK_SEARCH_URL = "https://www.seek.com.au/api/jobsearch/v5/search"
USER_AGENT = "OpenClaw-job-search/1.0 (+local automation; polite use)"
PAGE_DELAY_SECONDS = 0.75

# Human-readable labels from config.yaml → Seek houston worktype IDs.
WORK_TYPE_MAP: dict[str, str] = {
    "Full Time": "242",
    "Full time": "242",
    "Part Time": "243",
    "Part time": "243",
    "Contract/Temp": "244",
    "Contract / Temp": "244",
    "Casual/Vacation": "245",
    "Casual / Vacation": "245",
}


def _normalize_job(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map a raw Seek job object to the shared normalized dict shape."""
    job_id = raw.get("id")
    if job_id is None:
        return None

    advertiser = raw.get("advertiser") or {}
    company = advertiser.get("description") or ""
    locations = raw.get("locations") or []
    location = locations[0].get("label", "") if locations else ""

    return {
        "job_id": f"seek:{job_id}",
        "source": "seek",
        "title": raw.get("title") or "",
        "company": company,
        "location": location,
        "url": f"https://www.seek.com.au/job/{job_id}",
        "posted": raw.get("listingDate") or "",
    }


def _fetch_page(
    session: requests.Session,
    *,
    keyword: str,
    where: str,
    page: int,
    worktype: str | None,
) -> list[dict[str, Any]]:
    """Fetch and normalize one page of Seek results."""
    params: dict[str, str | int] = {
        "siteKey": "AU-Main",
        "sourcesystem": "houston",
        "where": where,
        "keywords": keyword,
        "page": page,
    }
    if worktype:
        params["worktype"] = worktype

    response = session.get(SEEK_SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning(
            "Seek returned non-JSON for keyword=%r where=%r page=%s: %s",
            keyword,
            where,
            page,
            exc,
        )
        return []

    data = payload.get("data") or []
    jobs: list[dict[str, Any]] = []
    for raw in data:
        normalized = _normalize_job(raw)
        if normalized:
            jobs.append(normalized)
    return jobs


def _resolve_work_types(work_types: list[str] | None) -> list[str | None]:
    """Return Seek API worktype IDs to query; [None] means no worktype filter."""
    if not work_types:
        return [None]

    resolved: list[str | None] = []
    for label in work_types:
        api_id = WORK_TYPE_MAP.get(label)
        if api_id:
            resolved.append(api_id)
        else:
            logger.warning("Unknown work type label %r — skipping", label)
    return resolved or [None]


def search(
    keyword: str,
    where: str,
    work_types: list[str] | None = None,
    max_pages: int = 2,
) -> list[dict]:
    """Search Seek for one keyword/location combination, optionally filtered by work type.

    Paginates up to ``max_pages`` per work-type variant. Returns normalized job dicts.
    On request failures, logs a warning and returns whatever was collected so far.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    for worktype in _resolve_work_types(work_types):
        for page in range(1, max_pages + 1):
            try:
                page_jobs = _fetch_page(
                    session,
                    keyword=keyword,
                    where=where,
                    page=page,
                    worktype=worktype,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Seek request failed keyword=%r where=%r worktype=%s page=%s: %s",
                    keyword,
                    where,
                    worktype,
                    page,
                    exc,
                )
                return all_jobs

            if not page_jobs:
                break

            for job in page_jobs:
                if job["job_id"] not in seen_ids:
                    seen_ids.add(job["job_id"])
                    all_jobs.append(job)

            if page < max_pages:
                time.sleep(PAGE_DELAY_SECONDS)

    return all_jobs
