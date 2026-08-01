"""Coverage QA — Google News + Talkwalker cross-check against the briefing.

WHAT THIS ANSWERS
    "What did the pipeline miss?" The collection stage reads a fixed feed list, so
    a relevant story published by an outlet not on that list is invisible to it.
    Google News and Talkwalker index by keyword instead of by publisher, which is
    precisely the blind spot the feed list has.

HOW IT DECIDES
    An article is added only if it clears two independent bars: it is not already
    in the briefing (fuzzy title match, since the same story carries different
    headlines across syndication), and it contains at least one FCC-domain keyword.
    Keyword search returns plenty of "FCC" that means Federal Credit Company or a
    football club - the relevance gate is not optional.

FAILURE POSTURE
    Additive only, and never fatal. Every failure path returns the briefing
    unchanged plus a log line. A QA outage must not stop a bulletin going out;
    a thinner briefing delivered on time beats no briefing.

COST
    $0.00 for the QA queries themselves - both sources are free RSS. The articles
    QA adds do get classified downstream, which is where the marginal cost lands.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("docuaction.bulletin.qa")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

QA_QUERIES = [
    '"FCC"',
    '"Brendan Carr"',
    '"Anna Gomez" OR "Geoffrey Starks"',
    '"spectrum auction" OR "C-band"',
    '"robocall" OR "TCPA"',
    '"BEAD broadband"',
    '"Starlink" OR "AST SpaceMobile"',
    '"broadcast license" OR "media ownership"',
]

# An article must mention at least one of these to be worth adding. Without this
# gate, a query for "FCC" returns Florida Citrus Commission and Football Club
# stories, and QA quietly degrades the briefing it is supposed to protect.
FCC_KEYWORDS = (
    "fcc", "federal communications", "spectrum", "broadband", "robocall", "tcpa",
    "broadcast", "satellite", "telecom", "wireless", "5g", "6g", "c-band", "cbrs",
    "bead", "starlink", "spacemobile", "carrier", "net neutrality", "usf",
    "universal service", "e-rate", "lifeline", "rdof", "emergency alert", "911",
)

TITLE_MATCH_THRESHOLD = 0.80
REQUEST_TIMEOUT = 20
CONCURRENCY = 4

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalise(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, drop a trailing
    publisher suffix. Google News appends ' - Publisher' to every headline, which
    would otherwise defeat similarity matching against the same story from RSS."""
    t = (title or "").lower()
    t = re.sub(r"\s+-\s+[^-]{2,40}$", "", t)
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def is_relevant(article: Dict[str, Any]) -> bool:
    blob = f"{article.get('title','')} {article.get('summary','')}".lower()
    return any(k in blob for k in FCC_KEYWORDS)


def titles_match(a: str, b: str, threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Syndicated copy is often a strict prefix of the original.
    if len(na) > 25 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _within_hours(published: str, hours: int) -> bool:
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


def _parse_google_rss(xml_text: str, query: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        # defusedxml, not stdlib ElementTree: these parse XML fetched from remote
        # feeds we do not control. stdlib ET is vulnerable to entity-expansion
        # (billion-laughs / quadratic blowup), which on the unattended scheduler
        # would hang the collector rather than fail a request. Falls back to the
        # stdlib parser if defusedxml is somehow absent, so a missing dependency
        # degrades collection instead of breaking it.
        try:
            from defusedxml import ElementTree as ET
        except ImportError:  # pragma: no cover
            import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
    except Exception as exc:
        logger.debug(f"Google News parse failed for {query!r}: {type(exc).__name__}")
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
            "source_name": _text("source") or "Google News",
            "qa_source": "QA-GoogleNews",
            "qa_query": query,
        })
    return out


async def fetch_google_news(hours: int = 24,
                            queries: List[str] | None = None) -> List[Dict[str, Any]]:
    queries = queries or QA_QUERIES
    results: List[Dict[str, Any]] = []
    try:
        import httpx
    except Exception:
        logger.warning("httpx unavailable - Google News QA skipped")
        return results

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _one(client, q: str) -> List[Dict[str, Any]]:
        async with sem:
            try:
                url = GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(q))
                resp = await client.get(url, timeout=REQUEST_TIMEOUT,
                                        follow_redirects=True)
                if resp.status_code != 200:
                    logger.info(f"Google News {q!r} -> HTTP {resp.status_code}")
                    return []
                items = _parse_google_rss(resp.text, q)
                return [a for a in items if _within_hours(a.get("published_at", ""), hours)]
            except Exception as exc:
                logger.info(f"Google News {q!r} failed: {type(exc).__name__}")
                return []

    async with httpx.AsyncClient(
        headers={"User-Agent": "DocuAction-FCC-Bulletin/1.0 (+https://docuaction.io)"}
    ) as client:
        gathered = await asyncio.gather(*(_one(client, q) for q in queries),
                                        return_exceptions=True)

    for g in gathered:
        if isinstance(g, list):
            results.extend(g)
    return results


def _title_of(article: Any) -> str:
    if isinstance(article, dict):
        return article.get("title", "") or ""
    return getattr(article, "title", "") or ""


async def run_qa_verification(existing_articles: List[Any],
                              hours: int = 24) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Cross-check the briefing against keyword search.

    Returns (articles_to_add, report). Never raises: on any failure the first
    element is empty and the report says why.
    """
    report: Dict[str, Any] = {
        "qa_sources_checked": 0,
        "google_news_found": 0,
        "talkwalker_found": 0,
        "already_in_bulletin": 0,
        "added_from_qa": 0,
        "skipped_not_relevant": 0,
        "skipped_duplicate": 0,
        "errors": [],
    }

    google: List[Dict[str, Any]] = []
    talk: List[Dict[str, Any]] = []

    try:
        google = await fetch_google_news(hours)
        report["google_news_found"] = len(google)
        report["qa_sources_checked"] += len(QA_QUERIES)
    except Exception as exc:
        report["errors"].append(f"google_news: {type(exc).__name__}")
        logger.warning(f"QA Google News stage failed: {exc}")

    try:
        from app.bulletin_intelligence import fcc_talkwalker

        talk = await fcc_talkwalker.fetch_all(hours)
        report["talkwalker_found"] = len(talk)
        report["qa_sources_checked"] += len(fcc_talkwalker.ALL_QUERIES)
    except Exception as exc:
        report["errors"].append(f"talkwalker: {type(exc).__name__}")
        logger.warning(f"QA Talkwalker stage failed: {exc}")

    existing_titles = [_title_of(a) for a in (existing_articles or [])]
    existing_titles = [t for t in existing_titles if t]

    to_add: List[Dict[str, Any]] = []
    accepted_titles: List[str] = []
    seen_urls = set()

    for candidate in google + talk:
        url_key = (candidate.get("url") or "").split("?")[0].rstrip("/").lower()
        if url_key and url_key in seen_urls:
            report["skipped_duplicate"] += 1
            continue

        title = candidate.get("title", "")
        if any(titles_match(title, t) for t in existing_titles):
            report["already_in_bulletin"] += 1
            continue
        if any(titles_match(title, t) for t in accepted_titles):
            report["skipped_duplicate"] += 1
            continue
        if not is_relevant(candidate):
            report["skipped_not_relevant"] += 1
            continue

        if url_key:
            seen_urls.add(url_key)
        accepted_titles.append(title)
        to_add.append(candidate)

    report["added_from_qa"] = len(to_add)

    logger.info(
        "QA verification: sources=%s google=%s talkwalker=%s already=%s added=%s "
        "not_relevant=%s duplicate=%s errors=%s",
        report["qa_sources_checked"], report["google_news_found"],
        report["talkwalker_found"], report["already_in_bulletin"],
        report["added_from_qa"], report["skipped_not_relevant"],
        report["skipped_duplicate"], report["errors"] or "none",
    )
    return to_add, report
