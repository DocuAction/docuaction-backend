"""
GDELT TV 2.0 — FREE Broadcast/TV Monitoring
Replaces TVEyes ($9,600-$50,000+/yr) with $0 cost.
Searches closed-caption transcripts of CNN, Fox, MSNBC, CSPAN, Bloomberg + 163 stations.
No API key required.
"""
import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

GDELT_TV_URL = "https://api.gdeltproject.org/api/v2/tv/tv"

# FCC-relevant search queries
FCC_TV_QUERIES = [
    "FCC",
    '"Federal Communications Commission"',
    '"Brendan Carr"',
    '"spectrum auction"',
    '"net neutrality"',
    '"broadband"',
    '"robocall"',
    '"Emergency Alert System"',
]

# National cable + DC broadcast affiliates
MARKETS = ["National"]
NETWORKS = ["ABC", "CBS", "NBC"]


@dataclass
class BroadcastClip:
    station: str = ""
    show: str = ""
    date: str = ""
    snippet: str = ""
    url: str = ""
    thumbnail: str = ""
    source_type: str = "tv_broadcast"
    query: str = ""


async def search_gdelt_tv(
    query: str,
    market: str = "National",
    max_records: int = 50,
    last_24h: bool = True,
) -> List[BroadcastClip]:
    """Search GDELT TV 2.0 for broadcast clips matching query."""
    params = {
        "query": query,
        "mode": "clipgallery",
        "format": "json",
        "maxrecords": min(max_records, 250),
        "sort": "DateDesc",
        "datanorm": "perc",
    }
    if market:
        params["query"] += f' market:"{market}"'
    if last_24h:
        params["last24"] = "yes"

    clips = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                GDELT_TV_URL,
                params=params,
                headers={"User-Agent": "DocuAction-BulletinIntelligence/1.0"},
            )
            if resp.status_code == 429:
                logger.warning("GDELT TV rate limited — backing off 10s")
                await asyncio.sleep(10)
                return clips
            if resp.status_code != 200:
                logger.error(f"GDELT TV {resp.status_code}: {resp.text[:200]}")
                return clips

            data = resp.json()
            for clip in data.get("clips", []):
                clips.append(BroadcastClip(
                    station=clip.get("station", ""),
                    show=clip.get("show", ""),
                    date=clip.get("date", ""),
                    snippet=clip.get("snippet", ""),
                    url=clip.get("preview_url", ""),
                    thumbnail=clip.get("preview_thumb", ""),
                    query=query,
                ))
    except Exception as e:
        logger.error(f"GDELT TV error for '{query}': {e}")

    return clips


async def ingest_broadcast_gdelt(lookback_hours: int = 24) -> List[dict]:
    """
    Ingest broadcast clips from GDELT TV 2.0.
    Returns article-compatible dicts for the briefing engine.
    """
    all_clips = []
    seen_urls = set()

    for query in FCC_TV_QUERIES:
        # National cable
        clips = await search_gdelt_tv(query, market="National", last_24h=True)
        for c in clips:
            if c.url and c.url not in seen_urls:
                seen_urls.add(c.url)
                all_clips.append(c)
        await asyncio.sleep(2)  # Rate limit: ~1 req/5s

        # Broadcast network affiliates
        for net in NETWORKS:
            clips = await search_gdelt_tv(
                f"{query} network:{net}", market="", last_24h=True, max_records=10
            )
            for c in clips:
                if c.url and c.url not in seen_urls:
                    seen_urls.add(c.url)
                    all_clips.append(c)
            await asyncio.sleep(2)

    logger.info(f"GDELT TV: {len(all_clips)} broadcast clips collected")

    # Convert to article-compatible format
    articles = []
    for clip in all_clips:
        articles.append({
            "title": f"[{clip.station}] {clip.show}: {clip.snippet[:80]}...",
            "summary": clip.snippet,
            "url": clip.url,
            "source": clip.station,
            "published_at": clip.date,
            "source_type": "broadcast",
            "thumbnail": clip.thumbnail,
            "show": clip.show,
        })

    return articles


async def get_tv_volume(query: str = "FCC", days: int = 7) -> dict:
    """Get broadcast mention volume timeline."""
    params = {
        "query": query,
        "mode": "timelinevol",
        "format": "json",
        "datanorm": "perc",
        "timespan": f"{days}days",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                GDELT_TV_URL, params=params,
                headers={"User-Agent": "DocuAction-BulletinIntelligence/1.0"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"GDELT TV volume error: {e}")
    return {}
