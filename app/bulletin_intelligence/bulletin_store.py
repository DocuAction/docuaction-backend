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

    # ── Phase 0 additive tables (INERT: created if missing, no reader/writer yet) ──
    # These back later phases (run instrumentation, coverage assurance, delivery
    # log, audit). Creating them now is a no-op for current behavior. All TEXT ids
    # are app-supplied UUIDs, matching the existing bulletin_* convention.
    """CREATE TABLE IF NOT EXISTS bulletin_run_log (
         run_id        TEXT PRIMARY KEY,
         agency_id     TEXT,
         trigger       TEXT,
         started_at    TEXT,
         finished_at   TEXT,
         duration_ms   INTEGER,
         ingested      INTEGER,
         after_dedup   INTEGER,
         in_briefing   INTEGER,
         rejected      INTEGER,
         dupes_removed INTEGER,
         cluster_count INTEGER,
         status        TEXT,
         error         TEXT,
         coverage_json TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_run_log_agency ON bulletin_run_log(agency_id)",
    """CREATE TABLE IF NOT EXISTS bulletin_source_outcome (
         id           TEXT PRIMARY KEY,
         run_id       TEXT,
         source       TEXT,
         type         TEXT,
         tier         TEXT,
         attempted    BOOLEAN,
         succeeded    BOOLEAN,
         items        INTEGER,
         http_status  INTEGER,
         error        TEXT,
         response_ms  INTEGER,
         retries      INTEGER
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_source_outcome_run ON bulletin_source_outcome(run_id)",
    """CREATE TABLE IF NOT EXISTS bulletin_source_registry (
         source_id         TEXT PRIMARY KEY,
         name              TEXT,
         type              TEXT,
         tier              TEXT,
         importance_weight REAL,
         enabled           BOOLEAN DEFAULT TRUE,
         method            TEXT,
         url               TEXT,
         notes             TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS bulletin_delivery_log (
         id                  TEXT PRIMARY KEY,
         briefing_id         TEXT,
         agency_id           TEXT,
         sent_by             TEXT,
         sent_at             TEXT,
         recipients_json     TEXT,
         subject             TEXT,
         sendgrid_message_id TEXT,
         result              TEXT,
         per_recipient_json  TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_delivery_log_briefing ON bulletin_delivery_log(briefing_id)",
    """CREATE TABLE IF NOT EXISTS bulletin_audit_log (
         id           TEXT PRIMARY KEY,
         ts           TEXT,
         actor        TEXT,
         event_type   TEXT,
         entity_type  TEXT,
         entity_id    TEXT,
         action       TEXT,
         details_json TEXT,
         result       TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_audit_log_entity ON bulletin_audit_log(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_audit_log_event ON bulletin_audit_log(event_type)",
]

# Flipped to True once init_store() succeeds; gates write attempts so we don't
# spam the log when the DB is unreachable.
_enabled = False
# Last init exception (string) — surfaced in counts() for prod diagnosis when
# logs aren't reachable. Cleared on success.
_last_init_error = None


async def init_store(retries: int = 7, delay: float = 3.0) -> bool:
    """Create tables if needed. Returns True if persistence is available.

    Railway's Postgres is frequently not ready the instant the app boots. A
    single no-retry attempt loses that race and leaves persistence disabled for
    the whole process lifetime — which silently wipes the archive on every
    restart (nothing to hydrate). Retry like the users-schema migration in
    main.py does, so a transient boot-time DB blip doesn't permanently disable
    durable storage.
    """
    global _enabled, _last_init_error
    import asyncio
    for attempt in range(1, retries + 1):
        try:
            async with async_session_maker() as s:
                for ddl in _DDL:
                    await s.execute(text(ddl))
                await s.commit()
            _enabled = True
            _last_init_error = None
            logger.info(f"Bulletin store ready (Postgres) on attempt {attempt}")
            return _enabled
        except Exception as e:
            _last_init_error = f"{type(e).__name__}: {e}"
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


async def clear_articles() -> int:
    """Delete ALL persisted article rows (the rolling archive cache). Returns the
    number of rows removed, or -1 on error. The archive rebuilds on the next run."""
    if not _enabled:
        return 0
    try:
        async with async_session_maker() as s:
            res = await s.execute(text("DELETE FROM bulletin_articles"))
            await s.commit()
            return res.rowcount if res.rowcount is not None else 0
    except Exception as e:
        logger.warning(f"clear_articles failed: {e}")
        return -1


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


# ── Phase 3: audit log (append-only) ─────────────────────────────────────────
async def save_audit(row: Dict[str, Any]) -> bool:
    """Append one immutable row to bulletin_audit_log. Best-effort; never raises."""
    if not _enabled:
        return False
    try:
        async with async_session_maker() as s:
            await s.execute(
                text(
                    "INSERT INTO bulletin_audit_log "
                    "(id, ts, actor, event_type, entity_type, entity_id, action, details_json, result) "
                    "VALUES (:id, :ts, :actor, :event_type, :entity_type, :entity_id, :action, :details, :result)"
                ),
                {
                    "id": row.get("id"), "ts": row.get("ts"),
                    "actor": row.get("actor", ""), "event_type": row.get("event_type", ""),
                    "entity_type": row.get("entity_type", ""), "entity_id": row.get("entity_id", ""),
                    "action": row.get("action", ""), "details": json.dumps(row.get("details") or {}),
                    "result": row.get("result", "ok"),
                },
            )
            await s.commit()
        return True
    except Exception as e:
        logger.warning(f"save_audit failed: {e}")
        return False


async def load_audit(event_type: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    """Read recent audit rows, newest first (optional event_type filter)."""
    if not _enabled:
        return []
    try:
        q = ("SELECT id, ts, actor, event_type, entity_type, entity_id, action, details_json, result "
             "FROM bulletin_audit_log")
        params: Dict[str, Any] = {}
        if event_type:
            q += " WHERE event_type = :event_type"; params["event_type"] = event_type
        q += " ORDER BY ts DESC LIMIT :lim"
        params["lim"] = max(1, min(int(limit), 1000))
        async with async_session_maker() as s:
            rows = (await s.execute(text(q), params)).fetchall()
        out = []
        for r in rows:
            details = r[7]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            out.append({
                "id": str(r[0]), "ts": r[1], "actor": r[2], "event_type": r[3],
                "entity_type": r[4], "entity_id": r[5], "action": r[6],
                "details": details, "result": r[8],
            })
        return out
    except Exception as e:
        logger.warning(f"load_audit failed: {e}")
        return []


# ── Phase 4: run log + per-source outcomes ───────────────────────────────────
async def save_run_log(row: Dict[str, Any]) -> bool:
    if not _enabled:
        return False
    try:
        async with async_session_maker() as s:
            await s.execute(
                text(
                    "INSERT INTO bulletin_run_log (run_id, agency_id, trigger, started_at, finished_at, "
                    "duration_ms, ingested, after_dedup, in_briefing, rejected, dupes_removed, cluster_count, "
                    "status, error, coverage_json) VALUES (:run_id, :agency_id, :trigger, :started_at, :finished_at, "
                    ":duration_ms, :ingested, :after_dedup, :in_briefing, :rejected, :dupes_removed, :cluster_count, "
                    ":status, :error, :coverage_json) "
                    "ON CONFLICT (run_id) DO UPDATE SET finished_at=EXCLUDED.finished_at, "
                    "duration_ms=EXCLUDED.duration_ms, status=EXCLUDED.status, coverage_json=EXCLUDED.coverage_json"
                ),
                {**{k: row.get(k) for k in (
                    "run_id", "agency_id", "trigger", "started_at", "finished_at", "duration_ms",
                    "ingested", "after_dedup", "in_briefing", "rejected", "dupes_removed", "cluster_count",
                    "status", "error")},
                 "coverage_json": json.dumps(row.get("coverage") or {})},
            )
            await s.commit()
        return True
    except Exception as e:
        logger.warning(f"save_run_log failed: {e}")
        return False


async def save_source_outcomes(rows: List[Dict[str, Any]]) -> int:
    if not _enabled or not rows:
        return 0
    n = 0
    try:
        async with async_session_maker() as s:
            for r in rows:
                await s.execute(
                    text("INSERT INTO bulletin_source_outcome (id, run_id, source, type, tier, attempted, "
                         "succeeded, items, http_status, error, response_ms, retries) VALUES (:id, :run_id, "
                         ":source, :type, :tier, :attempted, :succeeded, :items, :http_status, :error, "
                         ":response_ms, :retries)"),
                    {"id": r.get("id"), "run_id": r.get("run_id"), "source": r.get("source"),
                     "type": r.get("type"), "tier": r.get("tier"), "attempted": r.get("attempted", True),
                     "succeeded": r.get("succeeded", True), "items": r.get("items"),
                     "http_status": r.get("http_status"), "error": r.get("error"),
                     "response_ms": r.get("response_ms"), "retries": r.get("retries", 0)},
                )
                n += 1
            await s.commit()
    except Exception as e:
        logger.warning(f"save_source_outcomes failed: {e}")
        return 0
    return n


async def load_run_logs(agency_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    if not _enabled:
        return []
    try:
        q = ("SELECT run_id, agency_id, trigger, started_at, finished_at, duration_ms, ingested, "
             "after_dedup, in_briefing, rejected, dupes_removed, cluster_count, status, error, coverage_json "
             "FROM bulletin_run_log")
        params: Dict[str, Any] = {}
        if agency_id:
            q += " WHERE agency_id = :ag"; params["ag"] = agency_id
        q += " ORDER BY started_at DESC LIMIT :lim"; params["lim"] = max(1, min(int(limit), 500))
        cols = ["run_id", "agency_id", "trigger", "started_at", "finished_at", "duration_ms", "ingested",
                "after_dedup", "in_briefing", "rejected", "dupes_removed", "cluster_count", "status", "error"]
        async with async_session_maker() as s:
            rows = (await s.execute(text(q), params)).fetchall()
        out = []
        for r in rows:
            d = {c: r[i] for i, c in enumerate(cols)}
            cj = r[14]
            if isinstance(cj, str):
                try:
                    cj = json.loads(cj)
                except Exception:
                    cj = {}
            d["coverage"] = cj
            out.append(d)
        return out
    except Exception as e:
        logger.warning(f"load_run_logs failed: {e}")
        return []


async def load_source_outcomes(run_id: str) -> List[Dict[str, Any]]:
    if not _enabled:
        return []
    try:
        async with async_session_maker() as s:
            rows = (await s.execute(text(
                "SELECT source, type, tier, attempted, succeeded, items, http_status, error, response_ms, retries "
                "FROM bulletin_source_outcome WHERE run_id = :r ORDER BY source"), {"r": run_id})).fetchall()
        cols = ["source", "type", "tier", "attempted", "succeeded", "items", "http_status", "error",
                "response_ms", "retries"]
        return [{c: r[i] for i, c in enumerate(cols)} for r in rows]
    except Exception as e:
        logger.warning(f"load_source_outcomes failed: {e}")
        return []
