"""FCC Bulletin — Boolean search profiles (Phase 2). Additive + fallback-safe.

WHAT THIS CHANGES
    Boolean queries used for section matching were hardcoded in
    fcc_boolean_search.FCC_SEARCH_TOPICS. This module makes them database-driven so
    an editor can change a query without a code deploy, while keeping the constants
    as the fallback.

BEHAVIOUR GUARANTEE
    PROFILES starts as an exact copy of FCC_SEARCH_TOPICS at import time. If the DB
    is empty, unreachable, or refresh is never called, matching behaves exactly as
    before. The DB can only ever *override* with rows that were themselves seeded
    from the same constants.

WHY A MUTABLE MODULE-LEVEL DICT
    engine.py binds `_FCC_BOOL` to this dict once at import and `_boolean_section`
    reads it on every article. Refreshing IN PLACE (clear + update) means the engine
    picks up DB changes with no re-import, no signature change, and a 3-line diff in
    a 3,600-line file.

SCOPE — read this before assuming more was done
    This module makes the RETRIEVAL/section-matching queries database-driven. It does
    NOT unify the three competing keyword systems documented in
    bulletin-stabilization/02_keyword_systems.md. Those remain separate by design
    decision for this phase; only the conflict is documented.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger("docuaction.bulletin.profiles")

# Reading profiles from the DB is opt-in. OFF => import-time constants only, i.e.
# byte-identical behaviour to before Phase 2.
PROFILES_DB_ENABLED = (
    os.getenv("BULLETIN_PROFILES_DB_ENABLED", "false").strip().lower() == "true"
)

DEFAULT_AGENCY = "fcc"

# ── Fallback source of truth: the existing hardcoded constants ──────────────────
try:
    from app.bulletin_intelligence.fcc_boolean_search import FCC_SEARCH_TOPICS as _SEED
except Exception:  # pragma: no cover - defensive
    _SEED = {}

# The live dict the engine reads. Seeded from the constants; refreshed in place.
PROFILES: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in (_SEED or {}).items()}

# Provenance, surfaced by profiles_status() so operators can tell which source is live.
_source = "hardcoded"
_last_refresh: str = ""


def _seed_rows(agency_id: str = DEFAULT_AGENCY) -> List[Dict[str, Any]]:
    """Build DB rows from the hardcoded constants, verbatim.

    priority follows _BOOL_MATCH_ORDER semantics loosely (lower = evaluated earlier);
    it is metadata only in this phase — engine._BOOL_MATCH_ORDER still governs actual
    match precedence. Changing that ordering is deliberately NOT in scope here.
    """
    now = datetime.now(timezone.utc).isoformat()
    rows: List[Dict[str, Any]] = []
    for i, (key, val) in enumerate((_SEED or {}).items()):
        rows.append({
            "id": f"{agency_id}:{key}".lower(),
            "agency_id": agency_id,
            "profile_key": key,
            "name": (val or {}).get("label") or key,
            "boolean_query": (val or {}).get("boolean") or "",
            "description": "Seeded verbatim from fcc_boolean_search.FCC_SEARCH_TOPICS",
            "enabled": True,
            "priority": (i + 1) * 10,
            "created_at": now,
            "updated_at": now,
        })
    return rows


async def seed_defaults(agency_id: str = DEFAULT_AGENCY) -> int:
    """Idempotently insert the hardcoded topics as DB rows. Returns rows inserted.

    Safe to call repeatedly: existing (agency_id, profile_key) rows are left alone,
    so operator edits survive redeploys.
    """
    try:
        from app.bulletin_intelligence import bulletin_store
        return await bulletin_store.seed_search_profiles(_seed_rows(agency_id))
    except Exception as e:
        logger.warning(f"seed_defaults skipped: {e}")
        return 0


async def refresh_from_db(agency_id: str = DEFAULT_AGENCY) -> bool:
    """Replace PROFILES in place from the DB. Returns True if DB rows were applied.

    No-ops (leaving the hardcoded profiles active) when the feature flag is off, the
    store is unavailable, or the table is empty. Never raises.
    """
    global _source, _last_refresh
    if not PROFILES_DB_ENABLED:
        return False
    try:
        from app.bulletin_intelligence import bulletin_store
        rows = await bulletin_store.fetch_search_profiles(agency_id, enabled_only=True)
        if not rows:
            return False
        # Keep EVERY enabled row, including ones whose boolean_query is empty.
        # AI_MACHINE_LEARNING ships with an empty query in the hardcoded constants
        # (a pre-existing gap — see bulletin-stabilization/02_keyword_systems.md).
        # Filtering empties out would silently reduce 9 profiles to 8 and make the
        # DB path differ from the fallback in key count. _boolean_matches("") is
        # already False, so an empty query is harmless and preserving it keeps the
        # two paths exactly at parity — and leaves the slot visible for an operator
        # to fill in later.
        rebuilt = {
            r["profile_key"]: {"label": r.get("name") or r["profile_key"],
                               "boolean": r.get("boolean_query") or ""}
            for r in rows
        }
        if not rebuilt:
            return False
        # Mutate in place so engine._FCC_BOOL (same object) sees the update.
        PROFILES.clear()
        PROFILES.update(rebuilt)
        _source = "database"
        _last_refresh = datetime.now(timezone.utc).isoformat()
        logger.info(f"Boolean profiles refreshed from DB: {len(PROFILES)} active")
        return True
    except Exception as e:
        logger.warning(f"refresh_from_db skipped ({e}); keeping current profiles")
        return False


def profiles_status() -> Dict[str, Any]:
    """Diagnostics for GET /api/v1/bulletin/profiles."""
    return {
        "db_enabled": PROFILES_DB_ENABLED,
        "active_source": _source,
        "last_refresh": _last_refresh or None,
        "profile_count": len(PROFILES),
        "profile_keys": sorted(PROFILES.keys()),
        "fallback_count": len(_SEED or {}),
    }
