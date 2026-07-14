"""
FCC Bulletin — Coverage Hotfix (isolated, additive, fail-safe).

Purpose: reduce MISSED FCC stories without touching the collection pipeline's
structure. Everything here is:
  • Additive — it only PROTECTS/KEEPS stories and REPORTS; it never rejects.
  • Feature-flagged — BULLETIN_PRIORITY_PROTECT (default on; only adds coverage)
    and BULLETIN_BETTER_DEDUP (default off; opt-in).
  • Fail-safe — every public function is pure and defensive; callers wrap in
    try/except so any error leaves the normal bulletin flow untouched.

No schema, no migration, no API contract change, no shared module touched.
"""
import os
import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bulletin.coverage_hotfix")

PRIORITY_PROTECT = os.getenv("BULLETIN_PRIORITY_PROTECT", "true").strip().lower() != "false"
BETTER_DEDUP = os.getenv("BULLETIN_BETTER_DEDUP", "false").strip().lower() == "true"
# A priority story is "low confidence" (→ editorial review) below this score.
LOW_CONFIDENCE = float(os.getenv("BULLETIN_PRIORITY_LOW_CONF", "0.5"))

# High-priority FCC terms — stories mentioning these must never be dropped on
# low AI confidence alone (per editorial direction). Matched case-insensitively
# on word boundaries so short tokens (ABC, 911, Space) don't match inside words.
HIGH_PRIORITY_TERMS = [
    "AT&T", "Robocall", "TCPA", "E-Rate", "Pole Attachment", "Spectrum",
    "Wireless", "Subsea Cable", "Roku", "iHeart", "ABC", "The View", "Comcast",
    "FCC Enforcement", "Unauthorized Radio", "Foreign Ownership", "Chinese Telecom",
    "911", "Public Safety", "Cybersecurity", "Artificial Intelligence",
    "Satellite", "Space",
]


def _compile(term: str) -> re.Pattern:
    # AT&T / E-Rate contain regex-special chars → escape; keep word boundaries.
    return re.compile(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)")


_PATTERNS = [(t, _compile(t)) for t in HIGH_PRIORITY_TERMS]


def _blob(art) -> str:
    try:
        return f"{getattr(art, 'title', '') or ''} {getattr(art, 'summary', '') or ''}".lower()
    except Exception:
        return ""


def priority_term(art) -> Optional[str]:
    """Return the first matched high-priority term, or None. Pure; never raises."""
    if not PRIORITY_PROTECT:
        return None
    blob = _blob(art)
    if not blob:
        return None
    for term, pat in _PATTERNS:
        if pat.search(blob):
            return term
    return None


def is_high_priority(art) -> bool:
    return priority_term(art) is not None


def _provider_of(art) -> str:
    return str(getattr(art, "provider", None) or getattr(art, "source", None) or "?")


def _cat_of(art) -> str:
    return str(getattr(art, "topic", None) or "uncategorized")


def reclaim_priority(rejected: List[Any]) -> List[Any]:
    """From a list of stories an editorial filter REJECTED, return the ones that
    carry a high-priority term so the caller can re-add them. Logs each."""
    if not PRIORITY_PROTECT or not rejected:
        return []
    kept = []
    for art in rejected:
        term = priority_term(art)
        if term:
            kept.append(art)
            logger.info(
                "PRIORITY RECLAIM | provider=%s | term=%s | category=%s | headline=%s",
                _provider_of(art), term, _cat_of(art), (getattr(art, "title", "") or "")[:120],
            )
    if kept:
        logger.info(f"Priority protection reclaimed {len(kept)} story(ies) from editorial rejection")
    return kept


def briefing_keep_ids(classified: List[Any]) -> set:
    """article_ids of high-priority stories that must survive the briefing cutoff
    regardless of topic/relevance. Used to widen (never narrow) the briefing set."""
    if not PRIORITY_PROTECT:
        return set()
    ids = set()
    for a in classified:
        if is_high_priority(a):
            aid = getattr(a, "article_id", None)
            if aid:
                ids.add(aid)
    return ids


def _norm_url(url: str) -> str:
    """Normalize a URL for dedup: strip scheme, www, trailing slash, query/frag."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?", 1)[0].split("#", 1)[0]
    return u.rstrip("/")


def _fingerprint(art) -> str:
    title = re.sub(r"[^a-z0-9]+", "", (getattr(art, "title", "") or "").lower())[:80]
    dom = _norm_url(getattr(art, "url", "")).split("/", 1)[0]
    day = (getattr(art, "published_at", "") or getattr(art, "date", "") or "")[:10]
    return f"{dom}|{title}|{day}"


def better_deduplicate(articles: List[Any], base_dedup) -> List[Any]:
    """Opt-in stronger dedup (BULLETIN_BETTER_DEDUP=true). Runs the EXISTING
    deduplicate first (so behavior is a superset), then collapses remaining
    near-duplicates by normalized-URL and (domain+title+date) fingerprint.
    NEVER removes a story that has no earlier match — unique stories are always
    kept. Falls back to base_dedup on any error."""
    if not BETTER_DEDUP:
        return base_dedup(articles)
    try:
        unique = base_dedup(articles)
        seen_url, seen_fp, out = set(), set(), []
        for art in unique:
            nu = _norm_url(getattr(art, "url", ""))
            fp = _fingerprint(art)
            if nu and nu in seen_url:
                continue
            if fp in seen_fp:
                continue
            if nu:
                seen_url.add(nu)
            seen_fp.add(fp)
            out.append(art)
        logger.info(f"Better dedup: {len(unique)} → {len(out)} (URL+fingerprint)")
        return out
    except Exception as e:
        logger.warning(f"better_deduplicate failed, using base dedup: {e}")
        return base_dedup(articles)


def build_coverage_extra(all_articles: List[Any], unique: List[Any], briefing_arts: List[Any]) -> Dict[str, Any]:
    """Additive coverage/editorial analytics appended to the daily coverage report.
    Advisory only — flags items for editorial review; rejects nothing."""
    from collections import Counter
    provider_breakdown = dict(Counter(_provider_of(a) for a in all_articles))

    # Coverage gaps: a briefing story surfaced by exactly ONE provider — it would
    # have been MISSED if only that single provider ran. Flag for awareness.
    gaps = []
    for a in briefing_arts:
        provs = set()
        p = getattr(a, "providers", None)
        if isinstance(p, (list, set, tuple)):
            provs = {str(x) for x in p if x}
        if not provs:
            provs = {_provider_of(a)}
        if len(provs) == 1:
            gaps.append({
                "headline": (getattr(a, "title", "") or "")[:160],
                "source": next(iter(provs)),
                "category": _cat_of(a),
                "reason": "Surfaced by a single provider (would be missed without it)",
            })

    # Priority stories with low AI confidence → editorial review (kept, not rejected).
    low_conf_priority = []
    for a in briefing_arts:
        term = priority_term(a)
        if term and float(getattr(a, "relevance_score", 0) or 0) < LOW_CONFIDENCE:
            low_conf_priority.append({
                "headline": (getattr(a, "title", "") or "")[:160],
                "term": term,
                "category": _cat_of(a),
                "confidence": round(float(getattr(a, "relevance_score", 0) or 0), 2),
                "reason": "High-priority term with low AI confidence",
            })

    priority_in_briefing = sum(1 for a in briefing_arts if is_high_priority(a))
    editorial_review = gaps + low_conf_priority

    return {
        "priority_protection_enabled": PRIORITY_PROTECT,
        "better_dedup_enabled": BETTER_DEDUP,
        "priority_terms": len(HIGH_PRIORITY_TERMS),
        "priority_in_briefing": priority_in_briefing,
        "provider_breakdown": provider_breakdown,
        "coverage_gaps": gaps,
        "coverage_gap_count": len(gaps),
        "editorial_review": editorial_review,
        "editorial_review_count": len(editorial_review),
    }
