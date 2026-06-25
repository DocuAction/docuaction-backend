"""
DocuAction Bulletin Intelligence — BlueSky (AT Protocol) Ingestion
Pulls real FCC-related social posts from BlueSky's PUBLIC search endpoint.
No API key, no account, no OAuth — uses the unauthenticated public API.

Feeds the Social Media Summary with REAL engagement numbers:
  - likeCount, repostCount, replyCount per post
  - author handle + post text + timestamp

Endpoint:
  https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=<query>

This is a free supplement alongside YouTube. X (Twitter) remains a paid gap;
Reddit needs OAuth registration. BlueSky requires neither.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Any

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
TIMEOUT = httpx.Timeout(15.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DocuActionBulletin/1.0)",
    "Accept": "application/json",
}

# FCC-focused BlueSky search queries (kept tight to stay relevant)
BSKY_QUERIES = [
    "FCC",
    "Federal Communications Commission",
    "Brendan Carr FCC",
    "spectrum auction",
    "net neutrality FCC",
]


def _parse_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


async def ingest_bluesky(agency: Any, lookback_hours: int = 24,
                         make_article=None, hasher=None, now_iso=None,
                         is_relevant=None) -> List[Any]:
    """
    Pull recent FCC-related BlueSky posts (last `lookback_hours`).

    Dependency injection keeps this decoupled from engine internals:
      make_article(**fields) -> Article
      hasher(url, title) -> str
      now_iso() -> str
      is_relevant(title, summary) -> bool   (optional Boolean gate)

    Returns Article objects with source_type="social" carrying real
    like/repost/reply counts (stashed as bsky_* attrs for the social summary).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles: List[Any] = []
    seen = set()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for query in BSKY_QUERIES:
            try:
                resp = await client.get(SEARCH_URL, headers=HEADERS, params={
                    "q": query,
                    "limit": 25,
                    "sort": "latest",
                })
                if resp.status_code != 200:
                    logger.error(f"BlueSky search {resp.status_code}: {resp.text[:160]}")
                    continue

                posts = resp.json().get("posts", [])
                for p in posts:
                    record = p.get("record", {})
                    text = (record.get("text", "") or "").strip()
                    if not text:
                        continue

                    # Freshness filter
                    created = record.get("createdAt", "")
                    if created and _parse_ts(created) < cutoff:
                        continue

                    author = p.get("author", {})
                    handle = author.get("handle", "unknown")
                    display = author.get("displayName", handle)
                    uri = p.get("uri", "")
                    cid = p.get("cid", "")

                    # Build a viewable web URL from the at:// uri
                    # at://did/app.bsky.feed.post/<rkey>  →  bsky.app/profile/<handle>/post/<rkey>
                    rkey = uri.split("/")[-1] if uri else cid
                    web_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else f"https://bsky.app/profile/{handle}"

                    # Optional relevance gate
                    if is_relevant and not is_relevant(text, text):
                        # keep anyway if the query was the strict FCC phrase
                        if query == "FCC":
                            pass
                        else:
                            continue

                    dedup = hasher(web_url, text[:60]) if hasher else (cid or uri)
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    likes = int(p.get("likeCount", 0) or 0)
                    reposts = int(p.get("repostCount", 0) or 0)
                    replies = int(p.get("replyCount", 0) or 0)

                    title = text[:120]
                    summary = (f"{text[:240]}  [BlueSky · @{handle} · "
                               f"{likes:,} likes · {reposts:,} reposts · {replies:,} replies]")

                    art = make_article(
                        article_id=f"{agency.agency_id}_bsky_{dedup}",
                        agency_id=agency.agency_id,
                        source="bluesky",
                        source_type="social",
                        title=title,
                        url=web_url,
                        published_at=created or (now_iso() if now_iso else ""),
                        summary=summary,
                        full_text=text,
                        author=display,
                        outlet="BlueSky",
                        relevance_score=0.55,
                        ingested_at=now_iso() if now_iso else "",
                        dedup_hash=dedup,
                    )
                    setattr(art, "bsky_likes", likes)
                    setattr(art, "bsky_reposts", reposts)
                    setattr(art, "bsky_replies", replies)
                    # combined reach proxy for "highest reach" selection
                    setattr(art, "social_reach", likes + reposts + replies)
                    articles.append(art)

            except Exception as e:
                logger.error(f"BlueSky ingestion error for '{query}': {e}")

    logger.info(f"BlueSky: {len(articles)} posts for {agency.agency_id}")
    return articles
