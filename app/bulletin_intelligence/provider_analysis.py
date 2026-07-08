"""FCC Bulletin — provider coverage comparison + registry-diff editorial queue.

Additive, pure-Python analytics. Computes everything from REAL collected articles
and the loaded 194-source registry — never fabricates coverage numbers, never
auto-imports a source. Used by the Coverage Analysis and New Source Discovery /
Editorial Review deliverables.

Nothing here mutates the briefing or the registry. Safe to import anywhere.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


# ── URL canonicalization (for cross-provider dedup) ──────────────────────────
def canonical_url(url: str) -> str:
    """Normalize a URL for comparison: drop scheme, leading www., query, fragment,
    and trailing slash; lowercase host. Two providers linking the same article
    then collapse to the same key even if one adds tracking params."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u if "://" in u else "http://" + u)
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        return f"{host}{path}".lower()
    except Exception:
        return u.lower().rstrip("/")


def _fcc_relevant(art: Any) -> bool:
    """Delegate to the engine's 3-tier FCC relevance gate. Import is lazy so this
    module has no hard dependency on the engine at import time."""
    try:
        from app.bulletin_intelligence.engine import _is_fcc_relevant_v2
        return _is_fcc_relevant_v2(
            getattr(art, "title", "") or "",
            " ".join([getattr(art, "summary", "") or "", getattr(art, "full_text", "") or ""]),
        )
    except Exception:
        return True  # fail-open: never silently understate coverage


def _provider_of(art: Any) -> str:
    return (getattr(art, "provider", "") or getattr(art, "source", "") or "Unknown").strip()


# ── Coverage comparison: one provider vs everyone else ───────────────────────
def compare_provider_coverage(all_articles: List[Any],
                              target_provider: str = "NewsAPI.ai") -> Dict[str, Any]:
    """Compare `target_provider` against all OTHER providers, by canonical URL.

    Returns real counts of: unique-to-target, shared/duplicate, additional-FCC
    stories the target adds, and stories only the target surfaced (missed by the
    others). All derived from the actual collected set — no estimates.
    """
    target_urls: Dict[str, Any] = {}
    other_urls: Dict[str, Any] = {}
    for a in all_articles:
        cu = canonical_url(getattr(a, "url", ""))
        if not cu:
            continue
        if _provider_of(a).lower() == target_provider.lower():
            target_urls.setdefault(cu, a)
        else:
            other_urls.setdefault(cu, a)

    target_set = set(target_urls)
    other_set = set(other_urls)
    unique_keys = target_set - other_set
    shared_keys = target_set & other_set

    unique_articles = [target_urls[k] for k in unique_keys]
    additional_fcc = [a for a in unique_articles if _fcc_relevant(a)]

    return {
        "target_provider": target_provider,
        "target_collected": len(target_set),
        "other_collected": len(other_set),
        "unique_to_target": len(unique_keys),
        "duplicate_with_others": len(shared_keys),
        "additional_fcc_stories": len(additional_fcc),
        "stories_missed_by_others": len(unique_keys),  # only target had these URLs
        "unique_pct": round(100.0 * len(unique_keys) / len(target_set), 1) if target_set else None,
        # Small, honest samples for the report (headline + outlet only).
        "sample_unique": [
            {"title": getattr(a, "title", ""), "outlet": getattr(a, "outlet", ""),
             "url": getattr(a, "url", "")}
            for a in additional_fcc[:15]
        ],
        "note": ("Computed from real collected articles by canonical URL. "
                 "'stories_missed_by_others' = URLs only the target provider surfaced this run."),
    }


# ── Registry diff → editorial queue (no auto-import) ─────────────────────────
_NAME_CLEAN = re.compile(r"[^a-z0-9]+")


def _norm_name(name: str) -> str:
    return _NAME_CLEAN.sub("", (name or "").lower())


def _registry_index(registry: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index registry rows by normalized name AND by rss host, for matching."""
    idx: Dict[str, Dict[str, Any]] = {}
    for r in registry or []:
        nm = _norm_name(r.get("name", ""))
        if nm:
            idx[nm] = r
        host = canonical_url(r.get("rss_url", "")).split("/")[0]
        if host:
            idx.setdefault("host:" + host, r)
    return idx


def compare_against_registry(articles: List[Any],
                             registry: List[Dict[str, Any]],
                             target_provider: Optional[str] = "NewsAPI.ai") -> Dict[str, Any]:
    """Compare outlets DISCOVERED in a run (optionally only from target_provider)
    against the approved registry. Classifies each discovered outlet and produces
    an editorial queue. NEVER auto-imports — output is advisory only.

    Classifications:
      already_exists    — outlet matches an enabled registry source
      duplicate         — matches a registry source that is disabled (dupe/retired)
      new_source        — not in the registry at all
      potential_approval— new_source that carried >=2 FCC-relevant stories this run
      needs_review      — new_source with <2 FCC-relevant stories
    (A 'dead' verdict requires a live feed probe and is intentionally NOT asserted
     here — that is done by the collector's feed-health pass, not from run data.)
    """
    idx = _registry_index(registry or [])

    # Tally discovered outlets + how many FCC-relevant stories each carried.
    discovered: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"outlet": "", "articles": 0, "fcc_articles": 0, "providers": Counter(), "sample_url": ""}
    )
    for a in articles:
        if target_provider and _provider_of(a).lower() != target_provider.lower():
            continue
        outlet = (getattr(a, "source_name", "") or getattr(a, "outlet", "") or "").strip()
        if not outlet:
            continue
        key = _norm_name(outlet)
        d = discovered[key]
        d["outlet"] = d["outlet"] or outlet
        d["articles"] += 1
        d["providers"][_provider_of(a)] += 1
        if not d["sample_url"]:
            d["sample_url"] = getattr(a, "url", "") or ""
        if _fcc_relevant(a):
            d["fcc_articles"] += 1

    queue: List[Dict[str, Any]] = []
    counts: Counter = Counter()
    for key, d in discovered.items():
        reg = idx.get(key)
        if reg is not None:
            if reg.get("enabled", True):
                verdict = "already_exists"
            else:
                verdict = "duplicate"
        else:
            if d["fcc_articles"] >= 2:
                verdict = "potential_approval"
            else:
                verdict = "needs_review"
        counts[verdict] += 1
        queue.append({
            "outlet": d["outlet"],
            "verdict": verdict,
            "articles": d["articles"],
            "fcc_articles": d["fcc_articles"],
            "sample_url": d["sample_url"],
            "registry_match": reg.get("name") if reg else None,
            "registry_type": reg.get("type") if reg else None,
        })

    # Editorial queue = only the rows that need a human decision (never auto-import).
    editorial_queue = [q for q in queue
                       if q["verdict"] in ("new_source", "potential_approval", "needs_review", "duplicate")]
    editorial_queue.sort(key=lambda q: (q["verdict"] != "potential_approval", -q["fcc_articles"]))

    return {
        "target_provider": target_provider or "all",
        "registry_size": len(registry or []),
        "discovered_outlets": len(discovered),
        "counts": dict(counts),
        "editorial_queue": editorial_queue,
        "auto_import": False,
        "note": ("Advisory only — no source is auto-imported. 'dead' is not asserted "
                 "from run data; a live feed-health probe determines that separately."),
    }
