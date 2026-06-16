"""
GDELT DOC 2.0 — FREE Online News Article Search
No API key. Updated every 15 minutes. 65 languages. 250 articles/query.
"""
import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from typing import List

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

FCC_QUERIES = [
    "FCC",
    "Federal Communications Commission",
    "Brendan Carr FCC",
    "spectrum auction",
    "broadband policy",
    "robocall enforcement",
    "Emergency Alert System",
    "net neutrality",
]


async def search_gdelt_doc(
    query: str,
    mode: str = "artlist",
    max_records: int = 75,
    timespan: str = "24h",
    source_country: str = "US",
) -> List[dict]:
    """Search GDELT DOC 2.0 for online news articles."""
    params = {
        "query": query,
        "mode": mode,
        "format": "json",
        "maxrecords": min(max_records, 250),
        "timespan": timespan,
        "sort": "DateDesc",
    }
    if source_country:
        params["sourcecountry"] = source_country

    articles = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                GDELT_DOC_URL, params=params,
                headers={"User-Agent": "DocuAction-BulletinIntelligence/1.0"},
            )
            if resp.status_code == 429:
                logger.warning("GDELT DOC rate limited")
                await asyncio.sleep(10)
                return articles
            if resp.status_code != 200:
                return articles

            data = resp.json()
            for art in data.get("articles", []):
                articles.append({
                    "title": art.get("title", ""),
                    "url": art.get("url", ""),
                    "source": art.get("domain", art.get("source", "")),
                    "published_at": art.get("seendate", ""),
                    "language": art.get("language", "English"),
                    "source_type": "online_news",
                    "tone": art.get("tone", 0),
                })
    except Exception as e:
        logger.error(f"GDELT DOC error for '{query}': {e}")

    return articles


async def ingest_gdelt_doc(lookback_hours: int = 24) -> List[dict]:
    """Ingest FCC articles from GDELT DOC 2.0."""
    all_articles = []
    seen_urls = set()
    timespan = f"{lookback_hours}h" if lookback_hours <= 72 else "72h"

    for query in FCC_QUERIES:
        arts = await search_gdelt_doc(query, timespan=timespan)
        for a in arts:
            url = a.get("url", "").strip().rstrip("/").lower()
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_articles.append(a)
        await asyncio.sleep(2)

    logger.info(f"GDELT DOC: {len(all_articles)} online articles collected")
    return all_articles
