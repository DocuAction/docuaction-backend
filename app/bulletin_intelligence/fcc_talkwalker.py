"""Talkwalker Alerts RSS — free keyword monitoring for FCC coverage QA.

WHY THIS EXISTS
    The collection pipeline pulls from a fixed feed list. Anything published by an
    outlet not on that list is invisible to it, no matter how relevant. Talkwalker
    indexes the open web by keyword, so it surfaces the stories the feed list
    structurally cannot see.

WHAT IT IS NOT
    This is a QA signal, not a collection source. It reports what the pipeline
    missed; the caller decides what to do about it. Nothing here writes to the
    briefing.

FAILURE POSTURE
    Every function returns a list. A dead feed, a timeout, malformed XML, or
    Talkwalker being down produces an empty list and a log line, never an
    exception. QA is additive - a QA outage must never stop a briefing going out.

COST
    $0.00. Talkwalker Alerts RSS is free and unauthenticated.
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

logger = logging.getLogger("docuaction.bulletin.talkwalker")

TALKWALKER_RSS = "https://www.talkwalker.com/alerts/search?q={q}&lang=en&type=rss"

# VERIFIED 2026-07-29: the search-by-query URL above returns HTTP 404 for every
# query. Talkwalker does not expose a public query-to-RSS endpoint; a real alert
# feed is created in their UI and carries a per-alert token in its URL. The
# constant is kept because it documents what was tried and why it does not work.
#
# Supply real feeds through TALKWALKER_FEED_URLS as a comma-separated list:
#   TALKWALKER_FEED_URLS="https://www.talkwalker.com/alerts/rss/<token1>,https://...<token2>"
# When unset, this module contributes nothing and logs that it is unconfigured -
# which is the honest state, not a silent zero.
_CONFIGURED = [u.strip() for u in os.getenv("TALKWALKER_FEED_URLS", "").split(",") if u.strip()]

# Named commissioners. A story can be entirely about the Commission without the
# string "FCC" appearing anywhere in the headline - "Carr said Thursday..." is the
# normal way trade press writes it.
COMMISSIONER_QUERIES = [
    '"Brendan Carr" FCC',
    '"Anna Gomez" FCC',
    '"Geoffrey Starks" FCC',
    '"Nathan Simington" FCC',
    '"Olivia Trusty" FCC',
]

TOPIC_QUERIES = [
    "FCC spectrum auction",
    "FCC robocall enforcement",
    "FCC broadband BEAD",
    "FCC 5G satellite",
    "FCC broadcast license",
    "FCC emergency alert system",
    "TCPA enforcement action",
]

ALL_QUERIES = COMMISSIONER_QUERIES + TOPIC_QUERIES

REQUEST_TIMEOUT = 20
# Talkwalker is a courtesy service; 12 sequential-ish fetches with a small gap is
# well inside acceptable use and matches the pacing used elsewhere in this app.
CONCURRENCY = 4


def build_url(query: str) -> str:
    return TALKWALKER_RSS.format(q=urllib.parse.quote_plus(query))


def _parse_feed(xml_text: str, query: str) -> List[Dict[str, Any]]:
    """Parse an RSS body into article dicts. Never raises."""
    out: List[Dict[str, Any]] = []
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
    except Exception as exc:
        logger.debug(f"Talkwalker parse failed for {query!r}: {type(exc).__name__}")
        return out

    for item in root.iter():
        if not item.tag.endswith("item"):
            continue

        def _text(tag: str) -> str:
            for child in item:
                if child.tag.endswith(tag) and child.text:
                    return child.text.strip()
            return ""

        title, link = _text("title"), _text("link")
        if not title or not link:
            continue
        out.append({
            "title": title,
            "url": link,
            "published_at": _text("pubDate"),
            "source_name": _text("source") or "Talkwalker",
            "qa_source": "QA-Talkwalker",
            "qa_query": query,
        })
    return out


def _within_hours(published: str, hours: int) -> bool:
    """True when the timestamp is inside the window, or unparseable.

    Unparseable dates are kept deliberately. Dropping them would silently discard
    whole publishers whose RSS uses a format this parser does not recognise, and
    the caller's own deduplication is a cheaper filter than a false negative here.
    """
    if not published:
        return True
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(published)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except Exception:
        return True


async def fetch_query(client, query: str, hours: int = 24) -> List[Dict[str, Any]]:
    try:
        resp = await client.get(build_url(query), timeout=REQUEST_TIMEOUT,
                                follow_redirects=True)
        if resp.status_code != 200:
            logger.info(f"Talkwalker {query!r} -> HTTP {resp.status_code}, skipping")
            return []
        items = _parse_feed(resp.text, query)
        recent = [a for a in items if _within_hours(a.get("published_at", ""), hours)]
        logger.debug(f"Talkwalker {query!r}: {len(items)} items, {len(recent)} recent")
        return recent
    except Exception as exc:
        logger.info(f"Talkwalker {query!r} failed: {type(exc).__name__} - skipping")
        return []


def configured_feeds() -> List[str]:
    """Per-alert RSS URLs from TALKWALKER_FEED_URLS, if any."""
    return list(_CONFIGURED)


async def fetch_all(hours: int = 24,
                    queries: List[str] | None = None) -> List[Dict[str, Any]]:
    """Fetch every configured alert. Returns [] rather than raising, always.

    Prefers real per-alert feed URLs from TALKWALKER_FEED_URLS. Falls back to the
    query-search URL only when none are configured, which currently yields 404s -
    kept so the failure is visible in logs rather than silently absent.
    """
    if not _CONFIGURED:
        logger.info("Talkwalker QA: TALKWALKER_FEED_URLS is unset - no alert feeds "
                    "configured, contributing 0 articles. The query-search endpoint "
                    "returns 404 and is not a working substitute.")
        return []
    queries = queries or ALL_QUERIES
    results: List[Dict[str, Any]] = []
    try:
        import httpx
    except Exception:
        logger.warning("httpx unavailable - Talkwalker QA skipped entirely")
        return results

    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        headers={"User-Agent": "DocuAction-FCC-Bulletin/1.0 (+https://docuaction.io)"}
    ) as client:

        async def _one_url(u: str):
            async with sem:
                try:
                    resp = await client.get(u, timeout=REQUEST_TIMEOUT,
                                            follow_redirects=True)
                    if resp.status_code != 200:
                        logger.info(f"Talkwalker feed -> HTTP {resp.status_code}, skipping")
                        return []
                    items = _parse_feed(resp.text, "configured-alert")
                    return [a for a in items
                            if _within_hours(a.get("published_at", ""), hours)]
                except Exception as exc:
                    logger.info(f"Talkwalker feed failed: {type(exc).__name__}")
                    return []

        gathered = await asyncio.gather(*(_one_url(u) for u in _CONFIGURED),
                                        return_exceptions=True)

    for g in gathered:
        if isinstance(g, list):
            results.extend(g)

    # De-duplicate by URL within Talkwalker itself: commissioner and topic alerts
    # overlap heavily, and the same story routinely matches three queries.
    seen, unique = set(), []
    for a in results:
        key = (a.get("url") or "").split("?")[0].rstrip("/").lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(a)

    logger.info(f"Talkwalker QA: {len(queries)} queries, {len(results)} items, "
                f"{len(unique)} unique")
    return unique
