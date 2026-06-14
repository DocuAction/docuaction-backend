"""
DocuAction Bulletin Intelligence — YouTube Data API v3 Ingestion
Fills two FCC briefing gaps using a YOUTUBE_API_KEY:

  1. MEDIA / BROADCAST clips — news segments about the FCC that outlets and
     channels upload to YouTube (NPR, local TV/radio, news networks). These
     feed the Media & Broadcasting section as broadcast-type Articles.
  2. SOCIAL metrics — real view / like / comment counts for FCC-related videos,
     feeding the Social Media Summary's YouTube line with actual numbers
     instead of estimates.

NOTE: YouTube only sees what is UPLOADED to YouTube. It is a supplement to,
not a replacement for, true live TV/radio monitoring (TVEyes/Critical Mention).
The TVEyes swap point in ingest_broadcast remains for that.

Free quota: 10,000 units/day (search costs 100 units, videos.list ~1 unit),
so the daily cycle's handful of calls is well within free limits.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Any, Dict

import httpx

logger = logging.getLogger(__name__)

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY", "")
TIMEOUT = httpx.Timeout(20.0)
HEADERS = {"User-Agent": "DocuAction-BulletinIntelligence/1.0"}

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# FCC-focused YouTube search queries (kept tight to avoid noise/quota waste)
YT_QUERIES = [
    "FCC Federal Communications Commission",
    "FCC Brendan Carr",
    "FCC spectrum broadband",
]


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def ingest_youtube(agency: Any, lookback_hours: int = 24,
                         make_article=None, hasher=None, now_iso=None,
                         is_relevant=None) -> List[Any]:
    """
    Pull FCC-related YouTube videos from the last `lookback_hours`.

    Dependency injection keeps this module decoupled from engine internals:
      make_article(**fields) -> Article    (engine passes its Article ctor)
      hasher(url, title) -> str            (engine's _hash)
      now_iso() -> str                     (engine's _now)
      is_relevant(title, summary) -> bool  (boolean_filter.is_fcc_relevant)

    Returns a list of Article objects:
      - source_type="broadcast" for news-channel clips (Media & Broadcasting)
      - carries youtube view/like/comment counts in summary for social metrics
    """
    if not YOUTUBE_KEY:
        logger.info("YouTube: YOUTUBE_API_KEY not set, skipping")
        return []

    published_after = _iso_z(datetime.now(timezone.utc) - timedelta(hours=lookback_hours))
    articles: List[Any] = []
    seen = set()

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for query in YT_QUERIES:
            try:
                # 1) search for recent videos
                sresp = await client.get(SEARCH_URL, headers=HEADERS, params={
                    "key": YOUTUBE_KEY,
                    "q": query,
                    "part": "snippet",
                    "type": "video",
                    "order": "date",
                    "maxResults": 10,
                    "publishedAfter": published_after,
                    "relevanceLanguage": "en",
                })
                if sresp.status_code != 200:
                    logger.error(f"YouTube search {sresp.status_code}: {sresp.text[:160]}")
                    continue
                items = sresp.json().get("items", [])
                video_ids = [it["id"]["videoId"] for it in items
                             if it.get("id", {}).get("videoId")]
                if not video_ids:
                    continue

                # 2) fetch stats for those videos (real view/like/comment counts)
                vresp = await client.get(VIDEOS_URL, headers=HEADERS, params={
                    "key": YOUTUBE_KEY,
                    "id": ",".join(video_ids),
                    "part": "snippet,statistics",
                })
                if vresp.status_code != 200:
                    logger.error(f"YouTube videos {vresp.status_code}: {vresp.text[:160]}")
                    continue

                for v in vresp.json().get("items", []):
                    snip = v.get("snippet", {})
                    stats = v.get("statistics", {})
                    vid = v.get("id", "")
                    title = snip.get("title", "")
                    channel = snip.get("channelTitle", "YouTube")
                    if not title or not vid:
                        continue

                    url = f"https://www.youtube.com/watch?v={vid}"
                    # Boolean relevance gate (deterministic) if provided
                    if is_relevant and not is_relevant(title, title):
                        continue

                    dedup = hasher(url, title) if hasher else vid
                    if dedup in seen:
                        continue
                    seen.add(dedup)

                    views = int(stats.get("viewCount", 0) or 0)
                    likes = int(stats.get("likeCount", 0) or 0)
                    comments = int(stats.get("commentCount", 0) or 0)
                    desc = (snip.get("description", "") or "")[:300]

                    # News-channel uploads → broadcast section; others → social
                    lc = channel.lower()
                    is_news = any(k in lc for k in (
                        "news", "npr", "pbs", "cnn", "fox", "nbc", "cbs", "abc",
                        "c-span", "cspan", "bloomberg", "reuters", "associated press"))
                    stype = "broadcast" if is_news else "social"

                    summary = (f"{desc}  [YouTube · {channel} · "
                               f"{views:,} views · {likes:,} likes · {comments:,} comments]")

                    art = make_article(
                        article_id=f"{agency.agency_id}_yt_{dedup}",
                        agency_id=agency.agency_id,
                        source="youtube",
                        source_type=stype,
                        title=(f"[Broadcast] {title}" if stype == "broadcast" else title),
                        url=url,
                        published_at=snip.get("publishedAt", now_iso() if now_iso else ""),
                        summary=summary,
                        full_text=desc,
                        author=channel,
                        outlet=channel,
                        broadcast_clip_url=url if stype == "broadcast" else "",
                        relevance_score=0.6,
                        ingested_at=now_iso() if now_iso else "",
                        dedup_hash=dedup,
                    )
                    # stash raw metrics for the social summary builder
                    setattr(art, "yt_views", views)
                    setattr(art, "yt_likes", likes)
                    setattr(art, "yt_comments", comments)
                    articles.append(art)

            except Exception as e:
                logger.error(f"YouTube ingestion error for '{query}': {e}")

    logger.info(f"YouTube: {len(articles)} clips/posts for {agency.agency_id}")
    return articles
