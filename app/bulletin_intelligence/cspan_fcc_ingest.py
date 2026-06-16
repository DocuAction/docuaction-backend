"""
C-SPAN + FCC.gov + govinfo — FREE Primary Source Monitoring
Covers FCC open meetings, congressional hearings, Federal Register.
No paid APIs.
"""
import asyncio
import httpx
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

GOVINFO_KEY = os.getenv("GOVINFO_API_KEY", "DEMO_KEY")


async def ingest_fcc_gov() -> List[dict]:
    """Scrape FCC.gov for recent news releases and meeting documents."""
    articles = []
    urls_to_check = [
        ("https://www.fcc.gov/news-events/headlines", "FCC Headlines"),
        ("https://www.fcc.gov/document/daily-digest", "FCC Daily Digest"),
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # FCC RSS feed
            resp = await client.get(
                "https://www.fcc.gov/news-events/rss.xml",
                headers={"User-Agent": "DocuAction-BulletinIntelligence/1.0"},
            )
            if resp.status_code == 200:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    link = item.findtext("link", "")
                    desc = item.findtext("description", "")
                    pub = item.findtext("pubDate", "")
                    if title and link:
                        articles.append({
                            "title": title,
                            "url": link,
                            "source": "FCC.gov",
                            "published_at": pub,
                            "summary": desc[:500] if desc else "",
                            "source_type": "regulatory",
                        })
    except Exception as e:
        logger.error(f"FCC.gov ingest error: {e}")

    logger.info(f"FCC.gov: {len(articles)} items collected")
    return articles


async def ingest_govinfo_hearings(query: str = "Federal Communications Commission") -> List[dict]:
    """Search govinfo for congressional hearing transcripts mentioning FCC."""
    articles = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.govinfo.gov/search",
                params={
                    "query": query,
                    "collection": "CHRG",
                    "pageSize": 20,
                    "offsetMark": "*",
                    "api_key": GOVINFO_KEY,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for result in data.get("results", []):
                    articles.append({
                        "title": result.get("title", ""),
                        "url": result.get("detailsLink", ""),
                        "source": "Congress (govinfo)",
                        "published_at": result.get("dateIssued", ""),
                        "summary": result.get("title", ""),
                        "source_type": "regulatory",
                    })
    except Exception as e:
        logger.error(f"govinfo error: {e}")

    logger.info(f"govinfo hearings: {len(articles)} items")
    return articles


async def ingest_primary_sources() -> List[dict]:
    """Combine all free primary sources."""
    results = await asyncio.gather(
        ingest_fcc_gov(),
        ingest_govinfo_hearings(),
        return_exceptions=True,
    )
    articles = []
    for r in results:
        if isinstance(r, list):
            articles.extend(r)
    return articles
