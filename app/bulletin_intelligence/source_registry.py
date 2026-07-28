"""Bulletin Phase 4 - source registry enrichment and health monitoring.

EXTENDS the existing bulletin_source_registry table; it does not create a parallel
one. The table already carried 194 rows describing how each source is COLLECTED
(source_id, type, tier, method, url). Master_Source_Catalog.csv describes 122 sources
editorially (reliability, authority, duplicate risk, coverage). Those are two
different facts about the same things, so they belong in one row.

MATCHING IS ON DOMAIN, NOT NAME
    The two datasets were built independently: existing rows key on an internal
    source_id and carry a url; the catalogue keys on publication_name and website.
    Names diverge freely ("Reuters" vs "reuters-rss" vs "Reuters Top News"), domains
    do not. So the loader normalises both sides to a bare registrable domain and
    matches on that. A catalogue row with no domain match is INSERTED as a new
    registry entry rather than dropped.

IDEMPOTENT BY CONSTRUCTION
    Re-running the load updates the same rows and inserts nothing twice. Collection
    fields (type, tier, method, enabled) are never overwritten by the catalogue -
    only the editorial fields are - so a re-load cannot disturb collection behaviour.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("docuaction.bulletin.sources")

CATALOG_PATH = (Path(__file__).resolve().parents[2]
                / "docs" / "fcc-source-research" / "Master_Source_Catalog.csv")

# Sources that syndicate heavily; duplicate_risk High is the catalogue's own signal.
_WIRE_HINTS = ("wire", "syndicat", "newswire")

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def normalise_domain(value: str) -> str:
    """Reduce a URL or bare host to a comparable registrable domain.

    Strips scheme, credentials, port, path, and a leading 'www.'. Returns '' when
    nothing usable is present, so callers can distinguish "no domain" from a match.
    """
    v = (value or "").strip().lower()
    if not v:
        return ""
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)
    v = v.split("/")[0].split("?")[0]
    v = v.split("@")[-1].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v if "." in v else ""


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bool_wire(row: Dict[str, str]) -> bool:
    blob = f"{row.get('notes','')} {row.get('coverage_type','')}".lower()
    if any(h in blob for h in _WIRE_HINTS):
        return True
    # A High duplicate_risk on a national outlet is the catalogue's way of saying
    # "this content shows up everywhere", which is what wire_service is used for.
    return str(row.get("duplicate_risk", "")).strip().lower() == "high"


def _country_state(coverage_area: str) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort split of the catalogue's free-text coverage_area.

    The CSV has no country/state columns; coverage_area holds values like
    'US/Global', 'US', 'US-CA'. Anything not confidently parseable is left NULL
    rather than guessed - a wrong country on a source is worse than an absent one.
    """
    a = (coverage_area or "").strip()
    if not a:
        return None, None
    up = a.upper()
    state = None
    m = re.match(r"^US[-/ ]([A-Z]{2})$", up)
    if m:
        state = m.group(1)
    country = "US" if up.startswith("US") else ("Global" if "GLOBAL" in up else None)
    return country, state


def read_catalog(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Parse Master_Source_Catalog.csv into normalised registry rows."""
    p = Path(path or CATALOG_PATH)
    if not p.exists():
        logger.warning(f"Source catalogue not found at {p}")
        return []
    out: List[Dict[str, Any]] = []
    # utf-8-sig: the catalogue is Excel-exported and carries a BOM, which would
    # otherwise corrupt the first column name.
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            domain = normalise_domain(row.get("website", ""))
            name = (row.get("publication_name") or "").strip()
            if not name and not domain:
                continue
            country, state = _country_state(row.get("coverage_area", ""))
            out.append({
                "name": name,
                "domain": domain,
                "url": (row.get("website") or "").strip(),
                "country": country,
                "state": state,
                "language": "en",          # catalogue is US/English scope by design
                "media_type": (row.get("coverage_type") or "").strip() or None,
                "category": (row.get("primary_topics")
                             or row.get("editorial_focus") or "").strip() or None,
                "coverage_type": (row.get("coverage_type") or "").strip() or None,
                "reliability_score": _num(row.get("reliability_score")),
                "authority_score": _num(row.get("authority_score")),
                "duplicate_risk": (row.get("duplicate_risk") or "").strip() or None,
                "wire_service": _bool_wire(row),
                "fcc_relevance": (row.get("fcc_relevance") or "").strip() or None,
                "rss_feed": (row.get("rss_feed") or "").strip() or None,
                "notes": (row.get("notes") or "").strip() or None,
            })
    logger.info(f"Source catalogue: parsed {len(out)} rows from {p.name}")
    return out


# ── loading ───────────────────────────────────────────────────────────────────

async def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    """Idempotently merge the catalogue into bulletin_source_registry.

    Updates the editorial columns on a domain match; inserts a new row otherwise.
    Never touches type/tier/method/enabled/importance_weight - those describe how a
    source is COLLECTED and are owned by the collection config, not the catalogue.
    """
    from sqlalchemy import text
    from app.bulletin_intelligence import bulletin_store as store

    if not getattr(store, "_enabled", False):
        return {"loaded": False, "reason": "bulletin store not enabled"}

    rows = read_catalog(path)
    if not rows:
        return {"loaded": False, "reason": "catalogue empty or missing"}

    # Domain alone is NOT a unique key in this catalogue: fcc.gov appears 6 times
    # (Daily Digest, News Releases, ECFS, LMS, Auctions, Consumer Alerts),
    # politico.com twice, whitehouse.gov twice - 122 rows over 115 distinct domains.
    # Matching purely on domain would collapse six genuinely different FCC feeds into
    # one registry row and silently discard five, which matters most for exactly the
    # domain this product cares about. So a repeated domain falls back to a
    # domain+name key, while the single-domain majority still matches on domain.
    import collections as _c
    _dom_counts = _c.Counter(r["domain"] for r in rows if r["domain"])
    for r in rows:
        r["_multi"] = _dom_counts.get(r["domain"], 0) > 1
        r["_key"] = (f'{r["domain"]}#{re.sub(r"[^a-z0-9]+", "-", r["name"].lower()).strip("-")}'
                     if r["_multi"] else r["domain"])

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = inserted = skipped = 0

    async with store.async_session_maker() as s:
        existing = (await s.execute(text(
            "SELECT source_id, url, domain, name FROM bulletin_source_registry"
        ))).fetchall()
        # domain -> source_id, preferring an already-populated domain column and
        # falling back to one derived from the stored url.
        by_domain: Dict[str, str] = {}
        by_domain_name: Dict[str, str] = {}
        for sid, url, dom, _name in existing:
            key = normalise_domain(dom or "") or normalise_domain(url or "")
            if not key:
                continue
            if key not in by_domain:
                by_domain[key] = sid
            # Secondary index for multi-feed domains, so a catalogue feed can match a
            # pre-existing collection row of the same name instead of duplicating it.
            nk = f"{key}#{re.sub(r'[^a-z0-9]+', '-', (_name or '').lower()).strip('-')}"
            by_domain_name.setdefault(nk, sid)

        for r in rows:
            dom = r["domain"]
            # Multi-feed domains never match an existing row by domain - they must
            # each get their own entry, or five of the six FCC feeds vanish.
            sid = (by_domain_name.get(r["_key"]) if r["_multi"]
                   else (by_domain.get(dom) if dom else None))
            params = {
                "domain": dom or None, "country": r["country"], "state": r["state"],
                "language": r["language"], "media_type": r["media_type"],
                "category": r["category"], "coverage_type": r["coverage_type"],
                "reliability_score": r["reliability_score"],
                "authority_score": r["authority_score"],
                "duplicate_risk": r["duplicate_risk"],
                "wire_service": r["wire_service"],
                "fcc_relevance": r["fcc_relevance"], "rss_feed": r["rss_feed"],
                "loaded": now,
            }
            if sid:
                params["sid"] = sid
                await s.execute(text(
                    "UPDATE bulletin_source_registry SET "
                    "domain=COALESCE(:domain, domain), country=:country, state=:state, "
                    "language=:language, media_type=:media_type, category=:category, "
                    "coverage_type=:coverage_type, reliability_score=:reliability_score, "
                    "authority_score=:authority_score, duplicate_risk=:duplicate_risk, "
                    "wire_service=:wire_service, fcc_relevance=:fcc_relevance, "
                    "rss_feed=:rss_feed, catalog_loaded_at=:loaded "
                    "WHERE source_id=:sid"), params)
                updated += 1
            elif dom:
                params.update({"sid": f"catalog_{r['_key']}", "name": r["name"],
                               "url": r["url"], "notes": r["notes"]})
                await s.execute(text(
                    "INSERT INTO bulletin_source_registry "
                    "(source_id, name, url, notes, enabled, domain, country, state, "
                    " language, media_type, category, coverage_type, reliability_score, "
                    " authority_score, duplicate_risk, wire_service, fcc_relevance, "
                    " rss_feed, catalog_loaded_at, article_count) "
                    "VALUES (:sid, :name, :url, :notes, TRUE, :domain, :country, :state, "
                    " :language, :media_type, :category, :coverage_type, "
                    " :reliability_score, :authority_score, :duplicate_risk, "
                    " :wire_service, :fcc_relevance, :rss_feed, :loaded, 0) "
                    "ON CONFLICT (source_id) DO NOTHING"), params)
                if r["_multi"]:
                    by_domain_name[r["_key"]] = params["sid"]
                else:
                    by_domain[dom] = params["sid"]
                inserted += 1
            else:
                skipped += 1
        await s.commit()

    logger.info(f"Source catalogue load: {updated} updated, {inserted} inserted, "
                f"{skipped} skipped (no domain)")
    return {"loaded": True, "catalog_rows": len(rows), "updated": updated,
            "inserted": inserted, "skipped_no_domain": skipped, "at": now}


# ── health tracking ───────────────────────────────────────────────────────────

async def record_source_activity(articles: List[Any]) -> Dict[str, Any]:
    """Update last_seen / article_count for sources that produced articles.

    Called after a bulletin cycle. Additive and fail-soft: any error is logged and
    swallowed, because source bookkeeping must never be able to fail a briefing.
    """
    from sqlalchemy import text
    from app.bulletin_intelligence import bulletin_store as store

    if not getattr(store, "_enabled", False) or not articles:
        return {"updated": 0}

    counts: Dict[str, int] = {}
    for a in articles:
        dom = normalise_domain(getattr(a, "url", "") or "")
        if dom:
            counts[dom] = counts.get(dom, 0) + 1
    if not counts:
        return {"updated": 0}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = 0
    try:
        async with store.async_session_maker() as s:
            for dom, n in counts.items():
                res = await s.execute(text(
                    "UPDATE bulletin_source_registry SET "
                    "last_seen=:now, "
                    "first_seen=COALESCE(first_seen, :now), "
                    "article_count=COALESCE(article_count,0)+:n, "
                    "health_status='active' "
                    "WHERE domain=:dom"), {"now": now, "n": n, "dom": dom})
                updated += res.rowcount or 0
            await s.commit()
    except Exception as e:
        logger.warning(f"record_source_activity skipped: {e}")
        return {"updated": 0, "error": str(e)[:120]}
    return {"updated": updated, "domains": len(counts), "at": now}


# ── queries ───────────────────────────────────────────────────────────────────

_SELECT = ("source_id, name, domain, type, tier, media_type, category, country, "
           "state, language, reliability_score, authority_score, duplicate_risk, "
           "wire_service, fcc_relevance, rss_feed, enabled, first_seen, last_seen, "
           "article_count, health_status, url")


def _row(r) -> Dict[str, Any]:
    keys = [k.strip() for k in _SELECT.split(",")]
    return {k: (v if not isinstance(v, datetime) else v.isoformat())
            for k, v in zip(keys, r)}


async def fetch_sources(enabled_only: bool = False,
                        limit: int = 500) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    from app.bulletin_intelligence import bulletin_store as store
    if not getattr(store, "_enabled", False):
        return []
    q = f"SELECT {_SELECT} FROM bulletin_source_registry"
    if enabled_only:
        q += " WHERE enabled IS TRUE"
    q += " ORDER BY authority_score DESC NULLS LAST, name LIMIT :lim"
    async with store.async_session_maker() as s:
        rows = (await s.execute(text(q), {"lim": limit})).fetchall()
    return [_row(r) for r in rows]


async def source_health() -> Dict[str, Any]:
    """Aggregate health: how many sources are producing, silent, or never seen."""
    from sqlalchemy import text
    from app.bulletin_intelligence import bulletin_store as store
    if not getattr(store, "_enabled", False):
        return {"available": False, "reason": "bulletin store not enabled"}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    async with store.async_session_maker() as s:
        total = (await s.execute(text(
            "SELECT COUNT(*) FROM bulletin_source_registry"))).scalar() or 0
        with_catalog = (await s.execute(text(
            "SELECT COUNT(*) FROM bulletin_source_registry "
            "WHERE catalog_loaded_at IS NOT NULL"))).scalar() or 0
        ever = (await s.execute(text(
            "SELECT COUNT(*) FROM bulletin_source_registry "
            "WHERE last_seen IS NOT NULL"))).scalar() or 0
        active = (await s.execute(text(
            "SELECT COUNT(*) FROM bulletin_source_registry "
            "WHERE last_seen >= :c"), {"c": cutoff})).scalar() or 0
    return {
        "available": True,
        "total_sources": total,
        "enriched_from_catalog": with_catalog,
        "ever_produced": ever,
        "active_last_24h": active,
        "silent_last_24h": max(0, ever - active),
        "never_produced": max(0, total - ever),
        "cutoff_utc": cutoff,
        "note": ("'never_produced' counts sources with no recorded article ever. That "
                 "includes sources the collectors do not yet call, so it is a coverage "
                 "gap indicator, not a fault count."),
    }


async def missing_sources(hours: int = 24) -> Dict[str, Any]:
    """Sources that normally produce content but have produced nothing recently.

    'Normally produces' means it has a recorded first_seen and a non-zero lifetime
    article_count. A source that has never produced anything is NOT reported here -
    it is not missing, it was never present, and conflating the two would bury the
    real regressions.
    """
    from sqlalchemy import text
    from app.bulletin_intelligence import bulletin_store as store
    if not getattr(store, "_enabled", False):
        return {"available": False, "reason": "bulletin store not enabled"}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    async with store.async_session_maker() as s:
        rows = (await s.execute(text(
            "SELECT name, domain, article_count, last_seen, authority_score, tier "
            "FROM bulletin_source_registry "
            "WHERE last_seen IS NOT NULL AND COALESCE(article_count,0) > 0 "
            "  AND last_seen < :c "
            "ORDER BY authority_score DESC NULLS LAST, article_count DESC"),
            {"c": cutoff})).fetchall()
    items = [{"name": r[0], "domain": r[1], "lifetime_articles": r[2],
              "last_seen": r[3], "authority_score": r[4], "tier": r[5]} for r in rows]
    return {
        "available": True,
        "window_hours": hours,
        "cutoff_utc": cutoff,
        "missing_count": len(items),
        "severity": ("warning" if items else "ok"),
        "sources": items[:100],
        "note": ("Only sources with a prior production history are listed. Sources that "
                 "have never produced an article are excluded - they are a coverage "
                 "gap, not a regression."),
    }
