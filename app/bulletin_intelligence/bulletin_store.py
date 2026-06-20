"""
DocuAction Bulletin Intelligence — Durable Persistence (Postgres)

The engine keeps `_articles` / `_briefings` in memory as a working cache. Those
dicts reset on every Railway restart/redeploy, which wiped the 12-month archive
and the run history. This module mirrors that state to the app's already-
provisioned Postgres database so it survives restarts.

Design notes:
  - Raw SQL through the app's existing async engine — no ORM models, no Alembic
    migration needed (tables are created on startup with CREATE TABLE IF NOT
    EXISTS).
  - JSON payload stored in a TEXT column (predictable round-trip; avoids asyncpg
    JSONB decoding quirks). Key columns (agency_id, dates) are duplicated for
    indexing/filtering.
  - Graceful degradation: every function swallows and logs errors. If the DB is
    unavailable the daily cycle and API keep working off the in-memory cache —
    persistence is best-effort, never load-bearing for a request.
"""

import json
import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from app.core.database import async_session_maker

logger = logging.getLogger("docuaction.bulletin.store")

_DDL = [
    """CREATE TABLE IF NOT EXISTS bulletin_articles (
         article_id   TEXT PRIMARY KEY,
         agency_id    TEXT,
         published_at TEXT,
         ingested_at  TEXT,
         data         TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_articles_agency ON bulletin_articles(agency_id)",
    """CREATE TABLE IF NOT EXISTS bulletin_briefings (
         briefing_id  TEXT PRIMARY KEY,
         agency_id    TEXT,
         generated_at TEXT,
         data         TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_briefings_agency ON bulletin_briefings(agency_id)",
]

# Flipped to True once init_store() succeeds; gates write attempts so we don't
# spam the log when the DB is unreachable.
_enabled = False


async def init_store(retries: int = 7, delay: float = 3.0) -> bool:
    """Create tables if needed. Returns True if persistence is available.

    Railway's Postgres is frequently not ready the instant the app boots. A
    single no-retry attempt loses that race and leaves persistence disabled for
    the whole process lifetime — which silently wipes the archive on every
    restart (nothing to hydrate). Retry like the users-schema migration in
    main.py does, so a transient boot-time DB blip doesn't permanently disable
    durable storage.
    """
    global _enabled
    import asyncio
    for attempt in range(1, retries + 1):
        try:
            async with async_session_maker() as s:
                for ddl in _DDL:
                    await s.execute(text(ddl))
                await s.commit()
            _enabled = True
            logger.info(f"Bulletin store ready (Postgres) on attempt {attempt}")
            return _enabled
        except Exception as e:
            logger.warning(
                f"Bulletin store init attempt {attempt}/{retries} failed: {e}"
            )
            if attempt < retries:
                await asyncio.sleep(delay)
    _enabled = False
    logger.error("Bulletin store unavailable after retries; running memory-only")
    return _enabled


async def save_articles(articles: List[Dict[str, Any]]) -> int:
    if not _enabled or not articles:
        return 0
    saved = 0
    try:
        async with async_session_maker() as s:
            for a in articles:
                aid = a.get("article_id")
                if not aid:
                    continue
                await s.execute(
                    text(
                        """INSERT INTO bulletin_articles
                             (article_id, agency_id, published_at, ingested_at, data)
                           VALUES (:id, :ag, :pub, :ing, :data)
                           ON CONFLICT (article_id) DO UPDATE SET
                             agency_id    = EXCLUDED.agency_id,
                             published_at = EXCLUDED.published_at,
                             ingested_at  = EXCLUDED.ingested_at,
                             data         = EXCLUDED.data"""
                    ),
                    {
                        "id": aid,
                        "ag": a.get("agency_id", ""),
                        "pub": a.get("published_at", ""),
                        "ing": a.get("ingested_at", ""),
                        "data": json.dumps(a),
                    },
                )
                saved += 1
            await s.commit()
    except Exception as e:
        logger.warning(f"save_articles failed: {e}")
        return 0
    return saved


async def save_briefing(briefing: Dict[str, Any]) -> bool:
    if not _enabled:
        return False
    bid = briefing.get("briefing_id")
    if not bid:
        return False
    try:
        async with async_session_maker() as s:
            await s.execute(
                text(
                    """INSERT INTO bulletin_briefings
                         (briefing_id, agency_id, generated_at, data)
                       VALUES (:id, :ag, :gen, :data)
                       ON CONFLICT (briefing_id) DO UPDATE SET
                         agency_id    = EXCLUDED.agency_id,
                         generated_at = EXCLUDED.generated_at,
                         data         = EXCLUDED.data"""
                ),
                {
                    "id": bid,
                    "ag": briefing.get("agency_id", ""),
                    "gen": briefing.get("generated_at", ""),
                    "data": json.dumps(briefing),
                },
            )
            await s.commit()
        return True
    except Exception as e:
        logger.warning(f"save_briefing failed: {e}")
        return False


async def counts() -> Dict[str, Any]:
    """Lightweight persisted-record counts for health/observability."""
    if not _enabled:
        return {"enabled": False, "articles": 0, "briefings": 0}
    try:
        async with async_session_maker() as s:
            a = (await s.execute(text("SELECT COUNT(*) FROM bulletin_articles"))).scalar() or 0
            b = (await s.execute(text("SELECT COUNT(*) FROM bulletin_briefings"))).scalar() or 0
        return {"enabled": True, "articles": int(a), "briefings": int(b)}
    except Exception as e:
        logger.warning(f"counts failed: {e}")
        return {"enabled": True, "articles": 0, "briefings": 0, "error": str(e)}


async def load_all() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Load all persisted articles and briefings (as plain dicts)."""
    if not _enabled:
        return [], []
    try:
        async with async_session_maker() as s:
            art_rows = (await s.execute(text("SELECT data FROM bulletin_articles"))).fetchall()
            brief_rows = (await s.execute(text("SELECT data FROM bulletin_briefings"))).fetchall()

        def _decode(row):
            d = row[0]
            return d if isinstance(d, dict) else json.loads(d)

        articles = [_decode(r) for r in art_rows]
        briefings = [_decode(r) for r in brief_rows]
        return articles, briefings
    except Exception as e:
        logger.warning(f"load_all failed: {e}")
        return [], []
