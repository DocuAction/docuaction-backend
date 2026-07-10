"""
FCC Bulletin — Editor Audit (REPORTING ONLY, isolated, fail-safe).

After the bulletin is finalized, render a human-readable audit of exactly what
was collected, removed (with reasons), protected, gapped, and queued for review,
and append it to FCC_BULLETIN_EDITOR_AUDIT_YYYYMMDD.log.

Guarantees:
  • No behavior change — reads the pipeline's artifacts, changes nothing.
  • No filtering / collection / AI / API / schema / deployment change.
  • Fail-safe — every path is defensive; the caller wraps this in try/except so
    any error leaves the finalized bulletin untouched.
  • Flagged — BULLETIN_EDITOR_AUDIT (default on); BULLETIN_AUDIT_DIR sets the
    output directory (default ./logs).
"""
import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bulletin.editor_audit")

AUDIT_ENABLED = os.getenv("BULLETIN_EDITOR_AUDIT", "true").strip().lower() != "false"
AUDIT_DIR = os.getenv("BULLETIN_AUDIT_DIR", "logs")

PROVIDER_BUCKETS = ["Talkwalker", "NewsAPI.ai", "Event Registry", "RSS", "Government", "GDELT", "Other"]


def _provider(art) -> str:
    return str(getattr(art, "provider", None) or getattr(art, "source", None) or "?")


def _bucket(provider: str) -> str:
    p = (provider or "").lower()
    if "talkwalker" in p:
        return "Talkwalker"
    if "newsapi.ai" in p or "newsapi_ai" in p:
        return "NewsAPI.ai"
    if "event registry" in p or "eventregistry" in p:
        return "Event Registry"
    if "rss" in p:
        return "RSS"
    if any(g in p for g in ("gov", "fcc", "congress", "primary", "govinfo", "regulatory")):
        return "Government"
    if "gdelt" in p:
        return "GDELT"
    return "Other"


def _headline(art) -> str:
    return (getattr(art, "title", "") or "").strip()[:160] or "(no headline)"


def _aid(art) -> Any:
    return getattr(art, "article_id", None) or id(art)


def _fmt_rows(items: List[str]) -> str:
    return "\n".join(items) if items else "  (none)"


def build_report(agency_id, all_articles, unique, classified, briefing_arts, removed: Dict[str, List]) -> str:
    """Render the 5-section editor audit as text. Pure; never raises for data."""
    try:
        from app.bulletin_intelligence.coverage_hotfix import priority_term, build_coverage_extra
    except Exception:
        priority_term = lambda a: None  # noqa: E731
        build_coverage_extra = lambda *a, **k: {}  # noqa: E731

    now = datetime.now()
    L: List[str] = []
    L.append("=" * 72)
    L.append(f"FCC BULLETIN — EDITOR AUDIT   agency={agency_id}   {now.isoformat(timespec='seconds')}")
    L.append("=" * 72)

    # ── SECTION 1 — Articles Collected / Provider Breakdown ──
    from collections import Counter
    buckets = Counter(_bucket(_provider(a)) for a in all_articles)
    L.append("\nSECTION 1 — ARTICLES COLLECTED")
    L.append(f"  Total collected (post domain-exclusion): {len(all_articles)}")
    L.append(f"  Unique after dedup: {len(unique)}   Classified: {len(classified)}   In briefing: {len(briefing_arts)}")
    L.append("  Provider breakdown:")
    for b in PROVIDER_BUCKETS:
        L.append(f"    {b:<16} {buckets.get(b, 0)}")
    _extra_bucket = {k: v for k, v in buckets.items() if k not in PROVIDER_BUCKETS}
    for k, v in _extra_bucket.items():
        L.append(f"    {k:<16} {v}")

    # ── SECTION 2 — Articles Removed (with reason) ──
    L.append("\nSECTION 2 — ARTICLES REMOVED")
    total_removed = sum(len(v) for v in removed.values())
    L.append(f"  Total removed: {total_removed}")
    reason_of: Dict[Any, str] = {}
    for reason, arts in removed.items():
        L.append(f"  [{reason}] — {len(arts)}")
        for a in arts:
            reason_of[_aid(a)] = reason
            L.append(f"    - {_headline(a)}  | provider={_provider(a)} | reason={reason}")

    # ── SECTION 3 — High Priority Articles (Included / Rejected + reason) ──
    L.append("\nSECTION 3 — HIGH PRIORITY ARTICLES")
    included_ids = {_aid(a) for a in briefing_arts}
    seen = set()
    pool = list(briefing_arts) + [a for arts in removed.values() for a in arts]
    hp_count = 0
    for a in pool:
        aid = _aid(a)
        if aid in seen:
            continue
        seen.add(aid)
        term = priority_term(a)
        if not term:
            continue
        hp_count += 1
        if aid in included_ids:
            status, reason = "INCLUDED", "kept in briefing (priority-protected)"
        else:
            status, reason = "REJECTED", reason_of.get(aid, "not selected for briefing")
        L.append(f"    [{status}] term={term} | {_headline(a)} | reason={reason}")
    if hp_count == 0:
        L.append("  (no high-priority-term articles this run)")

    # ── SECTION 4 — Coverage Gap ──
    L.append("\nSECTION 4 — COVERAGE GAP")
    try:
        extra = build_coverage_extra(all_articles, unique, briefing_arts) or {}
    except Exception:
        extra = {}
    gaps = extra.get("coverage_gaps", []) or []
    L.append(f"  Coverage gaps flagged: {len(gaps)}")
    L.append(_fmt_rows([f"    - {g.get('headline', '')} | provider={g.get('source', '?')} | why={g.get('reason', '')}" for g in gaps]))

    # ── SECTION 5 — Editorial Queue (requires human review) ──
    L.append("\nSECTION 5 — EDITORIAL QUEUE (requires human review)")
    queue = extra.get("editorial_review", []) or []
    L.append(f"  Items requiring review: {len(queue)}")
    L.append(_fmt_rows([
        f"    - {q.get('headline', '')} | {q.get('reason', '')}"
        + (f" | term={q['term']}" if q.get('term') else "")
        + (f" | confidence={q['confidence']}" if q.get('confidence') is not None else "")
        for q in queue
    ]))

    L.append("\n" + "=" * 72 + "\n")
    return "\n".join(L)


def write_editor_audit(agency_id, all_articles, unique, classified, briefing_arts, removed: Dict[str, List]) -> Optional[str]:
    """Build + append the editor audit to today's log file. Returns the path, or
    None if disabled / on any error (logged). Never raises."""
    if not AUDIT_ENABLED:
        return None
    try:
        text = build_report(agency_id, all_articles, unique, classified, briefing_arts, removed or {})
    except Exception as e:
        logger.warning(f"Editor audit render failed: {e}")
        return None
    fname = f"FCC_BULLETIN_EDITOR_AUDIT_{datetime.now().strftime('%Y%m%d')}.log"
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        path = os.path.join(AUDIT_DIR, fname)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
        logger.info(f"Editor audit written: {path}")
        return path
    except Exception as e:
        # File write unavailable (e.g. read-only FS) — still emit to the app log.
        logger.warning(f"Editor audit file write failed ({e}); emitting to log instead:\n{text}")
        return None
