"""Bulletin Phase 5 - pre-generation quality gate.

WARN-ONLY BY DESIGN
    This gate NEVER blocks bulletin generation. A client who receives a thin briefing
    with an honest warning is better served than a client who receives nothing at
    all, and a silent quality regression is exactly what this is meant to surface.
    `passed` is therefore advisory; `blockers` exists in the contract but is only
    populated by conditions that mean the run produced literally nothing usable.

WHY IT RUNS AFTER COLLECTION, BEFORE RENDER
    Every check needs the collected article set, and all of them are cheap except the
    link spot-check, which is capped at 5 HEAD requests. Running here means a
    degraded run is labelled as such in the same cycle rather than discovered a day
    later.
"""

from __future__ import annotations

import logging
import os
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("docuaction.bulletin.quality")

MIN_ARTICLES = int(os.getenv("BULLETIN_MIN_ARTICLES", "10"))
MIN_PUBLISHERS = int(os.getenv("BULLETIN_MIN_PUBLISHERS", "3"))
MAX_DUPLICATE_PCT = float(os.getenv("BULLETIN_MAX_DUPLICATE_PCT", "60"))
LINK_SAMPLE = int(os.getenv("BULLETIN_LINK_SAMPLE", "5"))
LINK_FAIL_THRESHOLD = int(os.getenv("BULLETIN_LINK_FAIL_THRESHOLD", "2"))

GOV_DOMAINS = ("fcc.gov", "congress.gov", "whitehouse.gov", "ntia.gov", "ftc.gov")
NATIONAL = ("reuters.com", "apnews.com", "bloomberg.com", "wsj.com",
            "nytimes.com", "washingtonpost.com")
TRADE = ("broadbandbreakfast.com", "rcrwireless.com", "lightreading.com",
         "telecompetitor.com", "fiercewireless.com", "radioworld.com",
         "insideradio.com", "rbr.com", "cordcuttersnews.com", "telecomreseller.com")

# Last result per agency, surfaced at GET /quality/latest.
_last_quality: Dict[str, Dict[str, Any]] = {}


def _domain(url: str) -> str:
    v = (url or "").strip().lower()
    if not v:
        return ""
    v = v.split("://")[-1].split("/")[0].split("?")[0].split("@")[-1].split(":")[0]
    return v[4:] if v.startswith("www.") else v


def _check(name: str, passed: bool, **extra) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), **extra}


async def _link_spot_check(articles: List[Any]) -> Dict[str, Any]:
    """HEAD up to LINK_SAMPLE random article URLs.

    Deliberately small and best-effort. A 403/405 is NOT counted as broken: many
    publishers refuse HEAD or block non-browser agents, and treating that as a dead
    link would fire this warning on every run against exactly the outlets that matter
    most.
    """
    urls = [a.url for a in articles if getattr(a, "url", "").startswith("http")]
    if not urls:
        return {"sampled": 0, "failed": 0, "checked": [], "skipped": "no http urls"}
    sample = random.sample(urls, min(LINK_SAMPLE, len(urls)))
    failed, checked = 0, []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
            for u in sample:
                try:
                    r = await c.head(u)
                    code = r.status_code
                    if code in (403, 405, 429):          # publisher blocks HEAD
                        state = "blocked"
                    elif 200 <= code < 400:
                        state = "ok"
                    else:
                        state = "broken"
                        failed += 1
                except Exception as e:
                    state = f"error:{type(e).__name__}"
                    failed += 1
                checked.append({"domain": _domain(u), "state": state})
    except Exception as e:
        return {"sampled": 0, "failed": 0, "checked": [], "skipped": str(e)[:80]}
    return {"sampled": len(sample), "failed": failed, "checked": checked}


async def run_quality_gate(agency_id: str, collected: List[Any],
                           unique: List[Any], briefing_arts: List[Any],
                           window: Optional[Tuple[Any, Any]] = None,
                           providers: Optional[Dict[str, int]] = None,
                           check_links: bool = True) -> Dict[str, Any]:
    """Evaluate quality for one cycle. Never raises; never blocks."""
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    blockers: List[str] = []

    # 1. minimum articles
    n = len(briefing_arts)
    ok = n >= MIN_ARTICLES
    checks.append(_check("minimum_articles", ok, value=n, minimum=MIN_ARTICLES))
    if not ok:
        warnings.append(f"Only {n} article(s) in the briefing (minimum {MIN_ARTICLES}); "
                        f"the briefing is thin but was still generated")
    if n == 0:
        blockers.append("no articles reached the briefing - the cycle produced nothing")

    # 2. source diversity
    doms = [_domain(getattr(a, "url", "")) for a in briefing_arts]
    doms = [d for d in doms if d]
    pubs = len(set(doms))
    gov = sorted({d for d in doms if any(g in d for g in GOV_DOMAINS)})
    nat = sorted({d for d in doms if any(x in d for x in NATIONAL)})
    tra = sorted({d for d in doms if any(x in d for x in TRADE)})
    div_ok = pubs >= MIN_PUBLISHERS
    checks.append(_check("source_diversity", div_ok, publishers=pubs,
                         minimum=MIN_PUBLISHERS, government=gov, national=nat,
                         trade=tra))
    if not div_ok:
        warnings.append(f"Only {pubs} distinct publisher(s) (minimum {MIN_PUBLISHERS})")
    for label, found in (("government", gov), ("national", nat), ("trade", tra)):
        checks.append(_check(f"has_{label}_sources", bool(found), domains=found))
        if not found:
            warnings.append(f"No {label} sources in this briefing")

    # 3. duplicate percentage
    tot, uniq = len(collected), len(unique)
    dup_pct = round(100.0 * (tot - uniq) / tot, 1) if tot else 0.0
    dup_ok = dup_pct <= MAX_DUPLICATE_PCT
    checks.append(_check("duplicate_rate", dup_ok, percent=dup_pct,
                         collected=tot, unique=uniq, maximum=MAX_DUPLICATE_PCT))
    if not dup_ok:
        warnings.append(f"{dup_pct}% of collected articles were duplicates "
                        f"(over {MAX_DUPLICATE_PCT}%) - dedup or a provider may be "
                        f"misbehaving")

    # 4. date validation
    future = in_window = undated = 0
    now = datetime.now(timezone.utc)
    try:
        from app.bulletin_intelligence.engine import _parse_pub_dt
    except Exception:
        _parse_pub_dt = None
    for a in briefing_arts:
        d = _parse_pub_dt(getattr(a, "published_at", "") or "") if _parse_pub_dt else None
        if d is None:
            undated += 1
            continue
        if d > now + timedelta(hours=6):
            future += 1
        if window and window[0] <= d < window[1]:
            in_window += 1
    date_ok = future == 0
    checks.append(_check("date_validation", date_ok, future_dated=future,
                         undated=undated, in_window=in_window,
                         window=[str(window[0]), str(window[1])] if window else None))
    if future:
        warnings.append(f"{future} article(s) are future-dated by more than 6h")
    if undated:
        warnings.append(f"{undated} article(s) have an unparseable publish date")

    # 5. broken links (sampled)
    if check_links and briefing_arts:
        links = await _link_spot_check(briefing_arts)
        link_ok = links.get("failed", 0) <= LINK_FAIL_THRESHOLD
        checks.append(_check("link_spot_check", link_ok, **links))
        if not link_ok:
            warnings.append(f"{links['failed']} of {links['sampled']} sampled links "
                            f"failed (threshold {LINK_FAIL_THRESHOLD})")
    else:
        checks.append(_check("link_spot_check", True, skipped="disabled or no articles"))

    # 6. boolean profile coverage
    try:
        from app.bulletin_intelligence.profiles.boolean_profiles import PROFILES
        from app.bulletin_intelligence.engine import _boolean_section
        matched: Counter = Counter()
        for a in briefing_arts:
            sec = _boolean_section(getattr(a, "title", "") or "",
                                   getattr(a, "summary", "") or "")
            if sec:
                matched[sec] += 1
        enabled = [k for k, v in (PROFILES or {}).items()
                   if (v or {}).get("boolean", "").strip()]
        uncovered = [k for k in enabled if matched.get(k, 0) == 0
                     and matched.get((v := (PROFILES.get(k) or {})).get("section", k), 0) == 0]
        cov_ok = len(uncovered) < max(1, len(enabled) // 2)
        checks.append(_check("boolean_coverage", cov_ok, profiles=len(enabled),
                             uncovered=uncovered, matched=dict(matched)))
        for u in uncovered:
            warnings.append(f"Boolean profile {u} matched 0 articles")
    except Exception as e:
        checks.append(_check("boolean_coverage", True, skipped=str(e)[:80]))

    # 7. provider health
    prov = providers or Counter(
        (getattr(a, "provider", "") or getattr(a, "source", "") or "unknown")
        for a in collected)
    silent = sorted([p for p, c in prov.items() if not c])
    prov_ok = not silent
    checks.append(_check("provider_health", prov_ok, providers=dict(prov),
                         silent=silent))
    for p in silent:
        warnings.append(f"Provider {p} returned 0 articles")

    passed_n = sum(1 for c in checks if c["passed"])
    score = round(100.0 * passed_n / len(checks)) if checks else 0
    result = {
        "agency_id": agency_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": not blockers and score >= 70,
        "score": score,
        "checks_passed": passed_n,
        "checks_total": len(checks),
        "checks": checks,
        "warnings": warnings,
        "blockers": blockers,
        "advisory_only": True,
        "note": ("This gate is WARN-ONLY and never blocks bulletin generation. "
                 "'passed' is advisory: a failing score means the briefing is "
                 "degraded, not that it was withheld."),
    }
    _last_quality[agency_id] = result
    logger.info(f"Quality gate [{agency_id}]: score {score}/100, "
                f"{passed_n}/{len(checks)} checks passed, "
                f"{len(warnings)} warning(s), {len(blockers)} blocker(s)")
    return result


def last_quality(agency_id: str = "fcc") -> Dict[str, Any]:
    r = _last_quality.get(agency_id)
    if not r:
        return {"available": False,
                "reason": f"no quality gate result recorded for '{agency_id}' yet - "
                          f"it is populated by the next bulletin run"}
    return {"available": True, **r}
