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

import hashlib
import logging
import os
import time
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

# ── Quota protection ────────────────────────────────────────────────────────
# The free tier is 150 requests/day and was being exhausted by ~06:00. The drain
# was amplification, not a loop in this module:
#
#   9 profiles x 3 cycle attempts (scheduler.MAX_CYCLE_ATTEMPTS) = 27 calls/cycle
#   ...and scheduler._run_cycle_with_retry counts a cycle as FAILED when it
#   returns no articles. Once the quota is gone, _fetch returns [] silently, the
#   cycle looks failed, the hourly watchdog re-runs it, and each re-run spends
#   another 27 calls. Exhaustion is self-reinforcing: 150/27 ~= 5.5 hours.
#
# Three independent guards, any one of which is sufficient to stop the bleed:
#   1. RESPONSE CACHE (24h) — a retry or watchdog re-run reuses the first answer
#      instead of re-billing it. This is what actually breaks the loop, taking a
#      failing day from 27+/hour down to 9 calls total.
#   2. PER-RUN CAP — bounds any single cycle regardless of profile count.
#   3. DAILY BUDGET — a hard ceiling below the tier limit; once tripped, the
#      provider goes quiet for the rest of the day rather than burning credit on
#      calls that will 429 anyway.
#
# Every guard degrades to "return what we have and log", never to an exception:
# a Perigon problem must never stop a bulletin.
CACHE_TTL_S = int(os.getenv("PERIGON_CACHE_TTL_S", str(24 * 3600)))
MAX_CALLS_PER_RUN = int(os.getenv("PERIGON_MAX_CALLS_PER_RUN", "50"))
DAILY_BUDGET = int(os.getenv("PERIGON_DAILY_BUDGET", "120"))  # under the 150 tier

# {cache_key: (expires_at_epoch, [articles])}
_cache: Dict[str, Any] = {}
# Rolls over on UTC date change. "exhausted" latches on a 429 so we stop probing.
_budget: Dict[str, Any] = {"date": None, "calls": 0, "exhausted": False}


def _cache_key(query: str, from_date: str) -> str:
    return hashlib.sha256(f"{from_date}|{query}".encode()).hexdigest()


def _cache_get(key: str):
    hit = _cache.get(key)
    if not hit:
        return None
    expires_at, payload = hit
    if time.time() >= expires_at:
        _cache.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload) -> None:
    _cache[key] = (time.time() + CACHE_TTL_S, payload)


def _budget_roll() -> None:
    """Reset the daily counter when the UTC date changes."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _budget["date"] != today:
        _budget.update({"date": today, "calls": 0, "exhausted": False})


def _budget_allows() -> bool:
    _budget_roll()
    if _budget["exhausted"]:
        return False
    return _budget["calls"] < DAILY_BUDGET


def budget_status() -> Dict[str, Any]:
    """Observable counters for the provider-health surface and tests."""
    _budget_roll()
    return {
        "date": _budget["date"],
        "calls_today": _budget["calls"],
        "daily_budget": DAILY_BUDGET,
        "remaining": max(0, DAILY_BUDGET - _budget["calls"]),
        "exhausted": _budget["exhausted"],
        "cache_entries": len(_cache),
        "cache_ttl_s": CACHE_TTL_S,
        "max_calls_per_run": MAX_CALLS_PER_RUN,
    }


def _reset_for_tests() -> None:
    """Clear cache and budget. Test-support only."""
    _cache.clear()
    _budget.update({"date": None, "calls": 0, "exhausted": False})

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
    """One Perigon query, behind the cache and the budget.

    Returns [] rather than raising on any failure — the caller treats an empty
    result as "this profile contributed nothing", which is exactly right when the
    quota is gone.
    """
    key = _cache_key(query, from_date)
    cached = _cache_get(key)
    if cached is not None:
        logger.debug("Perigon: cache hit (%s...)", key[:8])
        return cached

    if not _budget_allows():
        logger.warning(
            "Perigon: daily budget spent (%d/%d used, exhausted=%s) — skipping call "
            "and continuing with other sources",
            _budget["calls"], DAILY_BUDGET, _budget["exhausted"])
        return []

    params = {
        "apiKey": PERIGON_API_KEY,
        "q": query,
        "from": from_date,
        "language": "en",
        "country": "us",
        "sortBy": "relevance",
        "size": PAGE_SIZE,
    }
    _budget["calls"] += 1
    resp = await client.get(PERIGON_BASE, params=params)
    if resp.status_code == 429:
        # Latch: the tier is spent. Further calls today would only burn latency
        # and confirm what we already know.
        _budget["exhausted"] = True
        logger.warning("Perigon: HTTP 429 quota exceeded — provider disabled for "
                       "the rest of the day; bulletin continues on other sources")
        return []
    if resp.status_code != 200:
        logger.warning(f"Perigon HTTP {resp.status_code}: {resp.text[:160]}")
        return []
    rows = resp.json().get("articles", []) or []
    # Cache successes only. Caching a failure would suppress a legitimate retry
    # once the quota resets.
    _cache_put(key, rows)
    return rows


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
    calls_this_run = 0
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for p in profiles:
                q = _build_query(p["boolean"])
                if not q:
                    continue
                # Per-run ceiling. Cache hits are free and do not count toward it.
                if _cache_get(_cache_key(q, from_date)) is None:
                    if calls_this_run >= MAX_CALLS_PER_RUN:
                        logger.warning(
                            "Perigon: per-run cap of %d reached — remaining profiles "
                            "skipped this cycle", MAX_CALLS_PER_RUN)
                        break
                    calls_this_run += 1
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
        return {"provider": "perigon", "enabled": False, "reason": "PERIGON_API_KEY not set",
                "budget": budget_status()}
    # A health probe is a billable request. Anything polling this endpoint was
    # silently competing with the bulletin for the same 150/day, so the probe now
    # respects the budget and reports state instead of spending the last credits.
    if not _budget_allows():
        return {
            "provider": "perigon", "enabled": True, "status": "quota_exhausted",
            "note": "probe skipped to preserve quota; not an availability failure",
            "budget": budget_status(),
        }
    started = datetime.now(timezone.utc)
    try:
        _budget["calls"] += 1
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(PERIGON_BASE, params={
                "apiKey": PERIGON_API_KEY, "q": "FCC", "size": 1,
                "language": "en", "country": "us", "sortBy": "relevance",
            })
        if resp.status_code == 429:
            _budget["exhausted"] = True
        ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "provider": "perigon",
            "enabled": True,
            "status": "ok" if resp.status_code == 200 else "error",
            "http_status": resp.status_code,
            "response_ms": ms,
            # numResults is capped at 10000 by the API and is NOT a real count.
            "note": "numResults from Perigon is capped at 10000; not a true total",
            "budget": budget_status(),
        }
    except Exception as e:
        return {"provider": "perigon", "enabled": True, "status": "error",
                "error": str(e)[:200], "budget": budget_status()}
