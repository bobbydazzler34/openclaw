"""Daily retail job search orchestrator for Suhani."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from openclaw.skills.job_search_suhani import dedup, emailer
from openclaw.skills.job_search_suhani.sources import retailer_site, seek

logger = logging.getLogger(__name__)

PLACEHOLDER_RECIPIENT = "REPLACE_ME@example.com"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_config(config_path: Path) -> dict:
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _dedupe_batch(jobs: list[dict]) -> list[dict]:
    """Remove duplicate job_id entries within a single run's merged results."""
    by_id: dict[str, dict] = {}
    for job in jobs:
        by_id[job["job_id"]] = job
    return list(by_id.values())


def main(config_path: Path | None = None) -> int:
    """Run the full search → dedup → email → mark-seen pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    path = config_path or DEFAULT_CONFIG_PATH
    logger.info("Loading config from %s", path)
    config = _load_config(path)

    recipient = config.get("email", {}).get("recipient", "")
    if recipient == PLACEHOLDER_RECIPIENT or not recipient:
        logger.error(
            "email.recipient is still the placeholder — set Suhani's address in config.yaml"
        )
        return 1

    subject_prefix = config.get("email", {}).get(
        "subject_prefix",
        "Retail jobs",
    )
    search_cfg = config.get("search", {})
    locations: list[str] = search_cfg.get("locations") or []
    keywords: list[str] = search_cfg.get("keywords") or []
    work_types: list[str] = search_cfg.get("work_types") or []
    retailer_sites: list[dict] = config.get("retailer_sites") or []
    always_send: bool = bool(config.get("always_send", False))

    all_jobs: list[dict] = []
    search_count = 0

    for keyword in keywords:
        for location in locations:
            search_count += 1
            logger.info("Seek search: keyword=%r location=%r", keyword, location)
            results = seek.search(keyword, location, work_types=work_types)
            logger.info("  → %d result(s)", len(results))
            all_jobs.extend(results)

    for site_config in retailer_sites:
        name = site_config.get("name", "unknown")
        if not site_config.get("enabled"):
            logger.info("Retailer site %r disabled — skipping", name)
            continue
        logger.info("Retailer site search: %r", name)
        results = retailer_site.search(site_config)
        logger.info("  → %d result(s)", len(results))
        all_jobs.extend(results)

    raw_count = len(all_jobs)
    merged_jobs = _dedupe_batch(all_jobs)
    deduped_count = len(merged_jobs)

    logger.info(
        "Searches run: %d | raw results: %d | deduped: %d",
        search_count,
        raw_count,
        deduped_count,
    )

    try:
        new_jobs = dedup.filter_new(merged_jobs)
    except RuntimeError:
        logger.exception("Supabase configuration error")
        return 1
    except Exception:
        logger.exception("Supabase error during filter_new")
        return 1

    new_count = len(new_jobs)
    logger.info("New (not previously seen): %d", new_count)

    if new_jobs or always_send:
        try:
            emailer.send_digest(
                new_jobs,
                recipient,
                subject_prefix,
                allow_empty=always_send,
            )
        except RuntimeError:
            logger.exception("Email configuration error")
            return 1
        except Exception:
            logger.exception("Failed to send email digest")
            return 1
    else:
        logger.info("No new jobs and always_send=false — email skipped")

    try:
        dedup.mark_seen(merged_jobs)
    except Exception:
        logger.exception("Supabase error during mark_seen")
        return 1

    logger.info("Run complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
