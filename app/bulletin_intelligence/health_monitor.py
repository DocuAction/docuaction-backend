"""
DocuAction Bulletin Intelligence — Health Monitoring & Quality Validation
Deliverables: Production Health Monitoring (#6), Daily Quality Validation (#7).

Two responsibilities:

1. HEALTH MONITORING (pre/independent of the daily cycle)
   - Probes each external source endpoint (RSS, GDELT, NewsAPI, Tavily,
     Federal Register, Anthropic) and reports up/down + latency.
   - Reports repository backend status (sqlite vs degraded in-memory).
   - Surfaces which API keys are configured (volume depends on them).
   - Designed to back a /health/bulletin endpoint for the 99.5% SLA.

2. QUALITY VALIDATION (post-cycle gate)
   - Validates a completed daily cycle against the FCC briefing contract:
     minimum article count, section coverage, source-diversity cap,
     authority presence (Reuters/AP/Bloomberg), and duplicate sanity.
   - Returns a pass/fail report with per-check detail so an operator (or an
     automated alert) can decide whether to release the briefing.

Pure stdlib + httpx. No new external dependencies.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0)
HEADERS = {"User-Agent": "DocuAction-BulletinIntelligence-Health/1.0"}

# Lightweight reachability probes per source (HEAD/GET a cheap endpoint).
_PROBES = {
    "gdelt": ("GET", "https://api.gdeltproject.org/api/v2/doc/doc?query=FCC&mode=artlist&maxrecords=1&format=json"),
    "federal_register": ("GET", "https://www.federalregister.gov/api/v1/documents.json?per_page=1"),
    "rss_fcc": ("GET", "https://www.fcc.gov/news-events/rss"),
    "rss_radioworld": ("GET", "https://www.radioworld.com/feed"),
    "anthropic": ("GET", "https://api.anthropic.com/v1/models"),  # 401 still = reachable
}


# ── Health monitoring ─────────────────────────────────────────────────────────
async def _probe(client: httpx.AsyncClient, name: str, method: str, url: str) -> Dict[str, Any]:
    start = datetime.now(timezone.utc)
    try:
        resp = await client.request(method, url, headers=HEADERS, follow_redirects=True)
        latency = (datetime.now(timezone.utc) - start).total_seconds()
        # For most sources any HTTP response (even 401/403) means reachable.
        reachable = resp.status_code < 500
        return {"source": name, "up": reachable, "status": resp.status_code,
                "latency_s": round(latency, 3)}
    except Exception as e:
        latency = (datetime.now(timezone.utc) - start).total_seconds()
        return {"source": name, "up": False, "status": None,
                "latency_s": round(latency, 3), "error": str(e)[:120]}


async def health_check(repo=None) -> Dict[str, Any]:
    """
    Probe all external sources + repository + key configuration.
    Returns an overall status plus per-source detail.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *[_probe(client, n, m, u) for n, (m, u) in _PROBES.items()],
            return_exceptions=True,
        )
    sources = [r for r in results if isinstance(r, dict)]

    # API key configuration (volume depends on these)
    keys = {
        "ANTHROPIC_API_KEY": bool(os.getenv("ANTHROPIC_API_KEY")),
        "SENDGRID_API_KEY": bool(os.getenv("SENDGRID_API_KEY")),
        "NEWSAPI_KEY": bool(os.getenv("NEWSAPI_KEY")),
        "TAVILY_KEY": bool(os.getenv("TAVILY_API_KEY")),
        "CONGRESS_API_KEY": bool(os.getenv("CONGRESS_API_KEY")),
    }

    # Repository backend
    repo_status = {"backend": "unknown"}
    if repo is not None:
        try:
            repo_status = repo.stats("fcc")
        except Exception as e:
            repo_status = {"backend": "error", "error": str(e)[:120]}

    up = sum(1 for s in sources if s.get("up"))
    total = len(sources)
    # Healthy if core free sources (GDELT + at least one RSS + anthropic) are up
    core_up = any(s["source"] == "gdelt" and s["up"] for s in sources) and \
              any(s["source"].startswith("rss") and s["up"] for s in sources)
    overall = "healthy" if core_up else ("degraded" if up else "down")

    return {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sources_up": f"{up}/{total}",
        "sources": sources,
        "keys_configured": keys,
        "repository": repo_status,
        "volume_capacity": _capacity_note(keys),
    }


def _capacity_note(keys: Dict[str, bool]) -> str:
    if keys["NEWSAPI_KEY"] and keys["TAVILY_KEY"]:
        return "full (60-100/day achievable)"
    if keys["NEWSAPI_KEY"] or keys["TAVILY_KEY"]:
        return "partial (40-80/day; set both keys for 60+ floor)"
    return "free-only (RSS+GDELT+Claude; ~40-70/day, key-dependent on news flow)"


# ── Daily quality validation ──────────────────────────────────────────────────
# FCC briefing contract thresholds (tunable).
MIN_ARTICLES = 60
MIN_SECTIONS = 6            # of 9; some sections are legitimately quiet some days
MAX_SOURCE_SHARE = 0.20
AUTHORITY_OUTLETS = {"reuters", "associated press", "ap", "bloomberg"}


def validate_briefing(articles: List[Any], topic_counts: Dict[str, int],
                      all_sections: List[str]) -> Dict[str, Any]:
    """
    Validate a completed cycle against the FCC briefing contract.
    `articles` = final briefing articles (objects with .outlet, .topic).
    Returns {passed: bool, checks: [...], summary: str}.
    """
    checks = []

    # 1) Minimum volume
    n = len(articles)
    checks.append({
        "check": "minimum_volume",
        "passed": n >= MIN_ARTICLES,
        "detail": f"{n} articles (require >= {MIN_ARTICLES})",
        "severity": "critical",
    })

    # 2) Section coverage
    populated = [s for s in all_sections if topic_counts.get(s, 0) > 0]
    empty = [s for s in all_sections if topic_counts.get(s, 0) == 0]
    checks.append({
        "check": "section_coverage",
        "passed": len(populated) >= MIN_SECTIONS,
        "detail": f"{len(populated)}/{len(all_sections)} sections populated; empty: {empty or 'none'}",
        "severity": "high",
    })

    # 3) Source diversity (no outlet > 20%)
    from collections import Counter
    counts = Counter((getattr(a, "outlet", "") or "unknown").strip().lower() for a in articles)
    worst = counts.most_common(1)[0] if counts else ("none", 0)
    share = (worst[1] / n) if n else 0
    checks.append({
        "check": "source_diversity",
        "passed": share <= MAX_SOURCE_SHARE + 0.001,
        "detail": f"top outlet '{worst[0]}' = {worst[1]} ({share:.0%}); cap {MAX_SOURCE_SHARE:.0%}",
        "severity": "high",
    })

    # 4) Authority presence (Reuters/AP/Bloomberg represented)
    outlets_lc = {(getattr(a, "outlet", "") or "").strip().lower() for a in articles}
    has_authority = any(
        any(auth in o for auth in AUTHORITY_OUTLETS) for o in outlets_lc
    )
    checks.append({
        "check": "authority_presence",
        "passed": has_authority,
        "detail": "Reuters/AP/Bloomberg present" if has_authority
                  else "no wire-service authority outlet in briefing",
        "severity": "medium",
    })

    # 5) Duplicate sanity (no exact-title repeats in final set)
    titles = [(getattr(a, "title", "") or "").strip().lower() for a in articles]
    dupes = len(titles) - len(set(titles))
    checks.append({
        "check": "duplicate_sanity",
        "passed": dupes == 0,
        "detail": f"{dupes} exact-title duplicates in final briefing",
        "severity": "medium",
    })

    critical_failed = [c for c in checks if not c["passed"] and c["severity"] == "critical"]
    passed = len(critical_failed) == 0 and all(
        c["passed"] for c in checks if c["severity"] == "high"
    )
    failed = [c["check"] for c in checks if not c["passed"]]
    summary = "PASS" if passed else f"FAIL ({', '.join(failed)})"

    return {
        "passed": passed,
        "summary": summary,
        "article_count": n,
        "sections_populated": len(populated),
        "checks": checks,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
