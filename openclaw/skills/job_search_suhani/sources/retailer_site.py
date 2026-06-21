"""Retailer career-site scraper extension point (disabled by default in v1)."""

from __future__ import annotations

import logging
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "OpenClaw-job-search/1.0 (+local automation; polite use)"

ExtractorFn = Callable[[BeautifulSoup, dict[str, Any]], list[dict]]


def extract_kmart(soup: BeautifulSoup, site_config: dict[str, Any]) -> list[dict]:
    """Placeholder extractor for Kmart's SAP SuccessFactors career site.

    Each retailer's ATS renders job listings differently (Workday, SuccessFactors,
    Avature, etc.). Inspect the live HTML for the target search URL before writing
    a real extractor — selectors and pagination are site-specific and brittle.
    """
    logger.info(
        "Kmart extractor not implemented (url=%s) — enable after writing HTML parser",
        site_config.get("search_url"),
    )
    return []


EXTRACTORS: dict[str, ExtractorFn] = {
    "Kmart": extract_kmart,
}


def search(site_config: dict[str, Any]) -> list[dict]:
    """Fetch a retailer career page and run its registered extractor, if enabled."""
    if not site_config.get("enabled"):
        return []

    name = site_config.get("name", "")
    extractor = EXTRACTORS.get(name)
    if extractor is None:
        logger.warning("No extractor registered for retailer site %r", name)
        return []

    search_url = site_config.get("search_url", "")
    if not search_url:
        logger.warning("Retailer site %r has no search_url configured", name)
        return []

    try:
        response = requests.get(
            search_url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Retailer site fetch failed for %r: %s", name, exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    return extractor(soup, site_config)
