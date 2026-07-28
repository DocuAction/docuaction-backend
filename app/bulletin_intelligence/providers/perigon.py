"""FCC Bulletin — Perigon news provider (Phase 3). Additive + key-gated.

WHY THIS EXISTS
    Perigon was the only major provider with NO implementation: engine.py contained
    three comment lines ("TO REPLACE WITH PERIGON LATER") and nothing else — no
    client, no adapter, not in requirements.txt. Setting PERIGON_API_KEY previously
    did nothing. This is the build.

API FACTS — verified live against api.goperigon.com on 2026-07-27, not assumed
    * Endpoint  GET https://api.goperigon.com/v1/all,  auth via `apiKey` query param.
    * Boolean IS fully supported in `q`: AND, OR, NOT, parentheses, quoted phrases.
      Proven by self-contradiction: `broadband AND NOT broadband` -> 0 results, while
      `broadband` -> results. This matters because the existing FCC profiles are
      already written in exactly this dialect, so they pass through unchanged.
    * `language=en` and `country=us` genuinely filter (verified) — these carry the
      US/FCC-focus requirement server-side instead of post-hoc.
    * `from=YYYY-MM-DD` filters by publish date (verified).
    * `sortBy=relevance` is dramatically better than `date` for precision. With
      sortBy=date a bare q=FCC returns travel and contest articles; with relevance it
      returns actual FCC coverage.
    * `numResults` is CAPPED AT 10000 and returns 10000 for nearly any query — it is
      NOT a usable result count and must never be reported as one.
    * Response: {status, numResults, articles[]}, ~40 fields per article.

THE "FCC" AMBIGUITY — the reason a precise query matters
    "FCC" also means FC Cincinnati, the soccer club. A bare q=FCC returns headlines
    like "Tempers flare after Columbus Crew defeat FCC". This adapter therefore never
    issues a bare-keyword query: it uses the Boolean profiles from Phase 2, which are
    already scoped like `(FCC OR "Federal Communications Commission") AND (...)`, and
    additionally applies a NOT-exclusion for the sports sense.

WIRE-STORY DATA WE GET FOR FREE
    Perigon returns `reprint` (bool), `reprintGroupId` and `clusterId`. That is
    native republished-story lineage — directly useful to the Phase 4 clustering
    requirement. It is carried through on the Article so a later phase can use it,
    but this phase does NOT change dedup behaviour.

SAFETY
    Key-gated: no PERIGON_API_KEY -> ingest returns [] immediately, exactly like the
    other optional collectors. Never raises: all failures are caught and logged so a
    Perigon outage cannot stop a bulletin cycle.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("docuaction.bulletin.perigon")

PERIGON_API_KEY = os.getenv("PERIGON_API_KEY", "").strip()
PERIGON_ENABLED = bool(PERIGON_API_KEY)

PERIGON_BASE = "https://api.goperigon.com/v1/all"
TIMEOUT = float(os.getenv("PERIGON_TIMEOUT", "45"))
# Per-profile page size. Perigon's max page is larger, but the bulletin caps
# classification at BULLETIN_MAX_CLASSIFY anyway, so pulling more just adds latency.
PAGE_SIZE = int(os.getenv("PERIGON_PAGE_SIZE", "50"))
# How many Boolean profiles to run per cycle. Each is one HTTP call.
MAX_PROFILES = int(os.getenv("PERIGON_MAX_PROFILES", "9"))

# Excluded because "FCC" is also FC Cincinnati. Appended to every profile query.
_SPORTS_EXCLUSION = '(soccer OR "FC Cincinnati" OR MLS OR "Major League Soccer")'


def _iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _build_query(boolean_query: str) -> str:
    """Wrap a Phase-2 Boolean profile with the sports-sense exclusion.

    The profile strings are already in Perigon's dialect (AND/OR/NOT/parens/quotes),
    so they are passed through verbatim rather than translated — no lossy rewriting.
    """
    q = (boolean_query or "").strip()
    if not q:
        return ""
    # Collapse the multi-line formatting the profiles use; Perigon wants one line.
    q = " ".join(q.split())
    return f"({q}) AND NOT {_SPORTS_EXCLUSION}"


def _profiles() -> List[Dict[str, str]]:
    """Boolean profiles to query, from the Phase 2 store (DB-backed, with fallback)."""
    try:
        from app.bulletin_intelligence.profiles.boolean_profiles import PROFILES
        out = []
        for key, val in (PROFILES or {}).items():
            bq = (val or {}).get("boolean") or ""
            if bq.strip():
                out.append({"key": key, "boolean": bq})
        return out[:MAX_PROFILES]
    except Exception as e:
        logger.warning(f"Perigon: could not load profiles ({e})")
        return []


async def _fetch(client: httpx.AsyncClient, query: str, from_date: str) -> List[Dict[str, Any]]:
    params = {
        "apiKey": PERIGON_API_KEY,
        "q": query,
        "from": from_date,
        "language": "en",
        "country": "us",
        "sortBy": "relevance",
        "size": PAGE_SIZE,
    }
    resp = await client.get(PERIGON_BASE, params=params)
    if resp.status_code != 200:
        logger.warning(f"Perigon HTTP {resp.status_code}: {resp.text[:160]}")
        return []
    return resp.json().get("articles", []) or []


def _to_article(r: Dict[str, Any], agency_id: str, make_article, hasher, now_iso) -> Optional[Any]:
    """Map one Perigon record onto the engine's Article schema."""
    url = (r.get("url") or "").strip()
    title = (r.get("title") or "").strip()
    if not url or not title:
        return None

    src = r.get("source") or {}
    outlet = (src.get("domain") or "").strip() or "Perigon"
    # description is the short blurb; summary/content are longer. Prefer the richest
    # non-empty option for summary, and keep content for full_text.
    summary = (r.get("description") or r.get("shortSummary") or r.get("summary") or "")[:400]
    full_text = (r.get("content") or r.get("summary") or "")[:800]
    authors = r.get("authorsByline") or ""

    dedup = hasher(url, title)
    art = make_article(
        article_id=f"{agency_id}_perigon_{dedup}",
        agency_id=agency_id,
        source="perigon",
        source_type="news",
        title=title,
        url=url,
        published_at=r.get("pubDate") or r.get("addDate") or now_iso(),
        summary=summary,
        full_text=full_text,
        author=authors if isinstance(authors, str) else "",
        outlet=outlet,
        ingested_at=now_iso(),
        dedup_hash=dedup,
        provider="Perigon",
        provider_url=PERIGON_BASE,
        source_name=outlet,
        collection_method="news_api",
        collection_time=now_iso(),
    )
    # Perigon's native republished-story lineage. Carried for a later clustering
    # phase to consume; nothing in this phase changes dedup behaviour because of it.
    try:
        setattr(art, "_perigon_reprint", bool(r.get("reprint")))
        setattr(art, "_perigon_reprint_group", r.get("reprintGroupId") or "")
        setattr(art, "_perigon_cluster", r.get("clusterId") or "")
    except Exception:
        pass
    return art


async def ingest_perigon(agency, lookback_hours: int = 24, *,
                         make_article=None, hasher=None, now_iso=None) -> List[Any]:
    """Collect FCC-relevant US English articles from Perigon.

    Returns [] and logs — never raises — when the key is absent or the API misbehaves,
    matching the failure contract of the other optional collectors so a Perigon
    problem can never stop a bulletin cycle.
    """
    if not PERIGON_ENABLED:
        return []

    # Imported lazily to avoid a circular import at module load (engine imports
    # providers indirectly through the collector list).
    if make_article is None or hasher is None or now_iso is None:
        try:
            from app.bulletin_intelligence.engine import Article as _A, _hash as _h, _now as _n
            make_article = make_article or _A
            hasher = hasher or _h
            now_iso = now_iso or _n
        except Exception as e:
            logger.warning(f"Perigon: engine helpers unavailable ({e})")
            return []

    profiles = _profiles()
    if not profiles:
        logger.info("Perigon: no Boolean profiles available; skipping")
        return []

    from_date = _iso_date(datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours)))
    agency_id = getattr(agency, "agency_id", "fcc")

    out: List[Any] = []
    seen: set = set()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for p in profiles:
                q = _build_query(p["boolean"])
                if not q:
                    continue
                try:
                    rows = await _fetch(client, q, from_date)
                except Exception as e:
                    logger.warning(f"Perigon profile {p['key']} failed: {e}")
                    continue
                for r in rows:
                    art = _to_article(r, agency_id, make_article, hasher, now_iso)
                    if art is None or art.dedup_hash in seen:
                        continue
                    seen.add(art.dedup_hash)
                    out.append(art)
    except Exception as e:
        logger.error(f"Perigon ingest error: {e}")
        return out

    logger.info(f"Perigon: {len(out)} articles for {agency_id} across {len(profiles)} profiles")
    return out


async def perigon_health() -> Dict[str, Any]:
    """Lightweight reachability + auth probe for the provider-health surface."""
    if not PERIGON_ENABLED:
        return {"provider": "perigon", "enabled": False, "reason": "PERIGON_API_KEY not set"}
    started = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(PERIGON_BASE, params={
                "apiKey": PERIGON_API_KEY, "q": "FCC", "size": 1,
                "language": "en", "country": "us", "sortBy": "relevance",
            })
        ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "provider": "perigon",
            "enabled": True,
            "status": "ok" if resp.status_code == 200 else "error",
            "http_status": resp.status_code,
            "response_ms": ms,
            # numResults is capped at 10000 by the API and is NOT a real count.
            "note": "numResults from Perigon is capped at 10000; not a true total",
        }
    except Exception as e:
        return {"provider": "perigon", "enabled": True, "status": "error", "error": str(e)[:200]}
