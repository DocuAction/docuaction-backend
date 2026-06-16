"""
Reddit Monitoring — FREE tier (100 QPM with OAuth)
Monitors r/technology, r/telecom, r/cordcutters, r/broadband for FCC mentions.
Requires: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET env vars.
"""
import os
import asyncio
import httpx
import logging
from typing import List

logger = logging.getLogger(__name__)

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = "DocuAction-BulletinIntelligence/1.0"

SUBREDDITS = ["technology", "telecom", "cordcutters", "broadband", "privacy", "netsec"]
SEARCH_QUERIES = ["FCC", "Federal Communications Commission", "Brendan Carr", "spectrum auction"]


async def _get_reddit_token() -> str:
    """Get Reddit OAuth token."""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        logger.warning("Reddit credentials not set — skipping")
        return ""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": REDDIT_USER_AGENT},
            )
            if resp.status_code == 200:
                return resp.json().get("access_token", "")
    except Exception as e:
        logger.error(f"Reddit auth error: {e}")
    return ""


async def search_reddit(query: str, token: str, limit: int = 25) -> List[dict]:
    """Search Reddit for FCC-related posts."""
    posts = []
    if not token:
        return posts
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://oauth.reddit.com/search",
                params={"q": query, "sort": "new", "limit": limit, "t": "day"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": REDDIT_USER_AGENT,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    d = child.get("data", {})
                    posts.append({
                        "title": d.get("title", ""),
                        "url": f"https://reddit.com{d.get('permalink', '')}",
                        "source": f"r/{d.get('subreddit', '')}",
                        "published_at": str(d.get("created_utc", "")),
                        "summary": (d.get("selftext", "") or "")[:500],
                        "score": d.get("score", 0),
                        "num_comments": d.get("num_comments", 0),
                        "source_type": "social_reddit",
                    })
    except Exception as e:
        logger.error(f"Reddit search error for '{query}': {e}")
    return posts


async def ingest_reddit() -> List[dict]:
    """Ingest FCC-related Reddit posts."""
    token = await _get_reddit_token()
    if not token:
        return []

    all_posts = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        posts = await search_reddit(query, token)
        for p in posts:
            url = p.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_posts.append(p)
        await asyncio.sleep(1)

    logger.info(f"Reddit: {len(all_posts)} posts collected")
    return all_posts
