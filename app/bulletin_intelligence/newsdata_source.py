"""DocuAction Bulletin Intelligence — NewsData.io collector.

Runs ALONGSIDE the existing RSS and Perigon collectors, never in place of them.
Each provider indexes a different slice of the press: RSS gives the outlets we
name explicitly, NewsData gives broad aggregation with commercial-use rights on
the free tier. Overlap is expected and is removed downstream by dedup_hash — a
story surfaced by two providers is one story, and the dedup key is the URL, not
the provider.

Free tier: 200 credits/day. One credit is one request, not one article, so the
page size matters more than the query count. Requests are therefore batched by
query with a page cap rather than paginated to exhaustion.

API:  https://newsdata.io/api/1/latest?apikey=KEY&q=FCC&language=en

Without NEWSDATA_API_KEY this module is INERT — `collect()` returns [] and logs
once at INFO. It never raises, because a missing optional provider must not take
down a briefing that RSS can still produce on its own.
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://newsdata.io/api/1/latest"
TIMEOUT = httpx.Timeout(20.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DocuActionBulletin/1.0)",
    "Accept": "application/json",
}

# Kept deliberately tight. Each query costs a credit against a 200/day budget,
# and a broad query like "telecom" returns mostly noise the relevance gate then
# has to throw away — paying a credit to discard the result.
NEWSDATA_QUERIES = [
    "FCC",
    "Federal Communications Commission",
    "broadband policy",
    "spectrum auction",
    "net neutrality",
]

MAX_PAGES_PER_QUERY = 1     # one credit per query per run
PROVIDER = "NewsData.io"


def _key() -> str:
    return os.getenv("NEWSDATA_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_key())


def _dedup_hash(url: str, title: str) -> str:
    """URL first — the same story syndicated under a rewritten headline is still
    the same story, and titles differ across aggregators far more than URLs do."""
    basis = (url or "").strip().lower() or (title or "").strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _to_article(raw: Dict[str, Any], agency_id: str) -> Dict[str, Any]:
    """Map a NewsData record onto the bulletin Article shape.

    Returns a dict rather than an Article so this module does not import the
    engine — engine.py already imports a great deal, and a cycle here would be
    paid at every startup.
    """
    url = (raw.get("link") or "").strip()
    title = (raw.get("title") or "").strip()
    content = (raw.get("content") or "") or (raw.get("description") or "")
    creators = raw.get("creator") or []
    author = ", ".join(creators) if isinstance(creators, list) else str(creators or "")

    published = (raw.get("pubDate") or "").strip()
    if published and " " in published and "T" not in published:
        # NewsData returns "2026-08-02 04:15:00"; normalise to ISO-8601 so it
        # sorts and compares against RSS timestamps rather than beside them.
        published = published.replace(" ", "T") + "Z"

    return {
        "article_id": _dedup_hash(url, title),
        "agency_id": agency_id,
        "source": raw.get("source_id") or PROVIDER,
        "source_type": "api",
        "title": title,
        "url": url,
        "published_at": published or datetime.now(timezone.utc).isoformat(),
        "summary": (raw.get("description") or "")[:1000],
        "full_text": content or "",
        "author": author,
        "outlet": raw.get("source_name") or raw.get("source_id") or PROVIDER,
        "article_type": "news",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "dedup_hash": _dedup_hash(url, title),
        "provider": PROVIDER,
        "provider_url": BASE_URL,
    }


async def collect(agency_id: str = "fcc",
                  queries: List[str] | None = None) -> List[Dict[str, Any]]:
    """Collect articles. Returns [] (never raises) when unconfigured or failing."""
    key = _key()
    if not key:
        logger.info("NewsData.io skipped — NEWSDATA_API_KEY not set")
        return []

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    qs = queries or NEWSDATA_QUERIES

    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        for query in qs:
            params = {"apikey": key, "q": query, "language": "en"}
            for _page in range(MAX_PAGES_PER_QUERY):
                try:
                    resp = await client.get(BASE_URL, params=params)
                except Exception as exc:                      # noqa: BLE001
                    logger.warning("NewsData.io %r transport error: %s", query, exc)
                    break

                if resp.status_code == 429:
                    # Daily credit budget exhausted. Stop the whole run, not just
                    # this query — every remaining request would also 429, and
                    # burning them produces nothing but log noise.
                    logger.warning("NewsData.io rate limited (429) — stopping run")
                    return out
                if resp.status_code != 200:
                    logger.warning("NewsData.io %r HTTP %s", query, resp.status_code)
                    break

                try:
                    payload = resp.json()
                except Exception:                             # noqa: BLE001
                    logger.warning("NewsData.io %r returned non-JSON", query)
                    break

                if payload.get("status") != "success":
                    logger.warning("NewsData.io %r status=%s", query,
                                   payload.get("status"))
                    break

                for raw in payload.get("results") or []:
                    art = _to_article(raw, agency_id)
                    if not art["url"] or not art["title"]:
                        continue
                    if art["dedup_hash"] in seen:
                        continue
                    seen.add(art["dedup_hash"])
                    out.append(art)

                nxt = payload.get("nextPage")
                if not nxt:
                    break
                params["page"] = nxt

    logger.info("NewsData.io collected %d unique articles across %d queries",
                len(out), len(qs))
    return out


async def probe() -> Dict[str, Any]:
    """Health probe for the connector matrix. Reports what is true, not what is
    hoped: an unconfigured provider is `configured: False`, not `down`."""
    if not _key():
        return {"provider": PROVIDER, "configured": False, "live": False,
                "note": "NEWSDATA_API_KEY not set (free tier at newsdata.io)"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
            r = await client.get(BASE_URL, params={"apikey": _key(), "q": "FCC",
                                                   "language": "en"})
        return {"provider": PROVIDER, "configured": True,
                "live": r.status_code == 200, "http": r.status_code}
    except Exception as exc:                                   # noqa: BLE001
        return {"provider": PROVIDER, "configured": True, "live": False,
                "error": str(exc)}
