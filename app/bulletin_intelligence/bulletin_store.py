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
from datetime import datetime, timedelta, timezone
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
    # ── Phase 4: source registry enrichment ──────────────────────────────────
    # ADD COLUMN IF NOT EXISTS is idempotent, so these run safely on every boot and
    # are additive only: every column is nullable and nothing existing is modified or
    # dropped. The 194 rows already in this table keep working untouched; the new
    # fields simply stay NULL until the Master_Source_Catalog load populates them.
    """ALTER TABLE bulletin_source_registry
         ADD COLUMN IF NOT EXISTS domain            TEXT,
         ADD COLUMN IF NOT EXISTS country           TEXT,
         ADD COLUMN IF NOT EXISTS state             TEXT,
         ADD COLUMN IF NOT EXISTS language          TEXT,
         ADD COLUMN IF NOT EXISTS media_type        TEXT,
         ADD COLUMN IF NOT EXISTS category          TEXT,
         ADD COLUMN IF NOT EXISTS reliability_score REAL,
         ADD COLUMN IF NOT EXISTS authority_score   REAL,
         ADD COLUMN IF NOT EXISTS duplicate_risk    TEXT,
         ADD COLUMN IF NOT EXISTS wire_service      BOOLEAN,
         ADD COLUMN IF NOT EXISTS first_seen        TEXT,
         ADD COLUMN IF NOT EXISTS last_seen         TEXT,
         ADD COLUMN IF NOT EXISTS article_count     INTEGER DEFAULT 0,
         ADD COLUMN IF NOT EXISTS health_status     TEXT,
         ADD COLUMN IF NOT EXISTS coverage_type     TEXT,
         ADD COLUMN IF NOT EXISTS fcc_relevance     TEXT,
         ADD COLUMN IF NOT EXISTS rss_feed          TEXT,
         ADD COLUMN IF NOT EXISTS catalog_loaded_at TEXT""",
    """CREATE INDEX IF NOT EXISTS idx_bsr_domain ON bulletin_source_registry(domain)""",
    """CREATE INDEX IF NOT EXISTS idx_bsr_health ON bulletin_source_registry(health_status)""",
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
    # ── Claude API cost tracking (Phase 1) ──────────────────────────────────────
    # Additive: no existing table is altered. tokens_in/tokens_out are stored raw
    # alongside cost_usd so historical rows can be re-priced if Anthropic pricing
    # changes (cost_usd is only as good as the rate table in costs/cost_tracker.py).
    """CREATE TABLE IF NOT EXISTS bulletin_cost_logs (
         id          TEXT PRIMARY KEY,
         run_id      TEXT,
         agency_id   TEXT,
         operation   TEXT,
         provider    TEXT,
         model       TEXT,
         tokens_in   INTEGER,
         tokens_out  INTEGER,
         api_calls   INTEGER,
         cost_usd    DOUBLE PRECISION,
         created_at  TEXT
       )""",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_cost_logs_run ON bulletin_cost_logs(run_id)",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_cost_logs_created ON bulletin_cost_logs(created_at)",
    # ── Boolean search profiles (Phase 2) ───────────────────────────────────────
    # Additive. Seeded verbatim from fcc_boolean_search.FCC_SEARCH_TOPICS so the DB
    # starts byte-identical to the hardcoded queries; the code falls back to those
    # constants whenever this table is empty or unreachable, so an empty table is a
    # valid state, not a failure.
    """CREATE TABLE IF NOT EXISTS bulletin_search_profiles (
         id            TEXT PRIMARY KEY,
         agency_id     TEXT,
         profile_key   TEXT,
         name          TEXT,
         boolean_query TEXT,
         description   TEXT,
         enabled       BOOLEAN DEFAULT TRUE,
         priority      INTEGER DEFAULT 100,
         created_at    TEXT,
         updated_at    TEXT
       )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bulletin_search_profiles_key "
    "ON bulletin_search_profiles(agency_id, profile_key)",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_search_profiles_enabled "
    "ON bulletin_search_profiles(agency_id, enabled)",
    # ── Distribution list (Task 3.5) ────────────────────────────────────────────
    # Additive, and deliberately NOT authoritative yet: AgencyConfig.distribution_list
    # remains what send_briefing_email reads. This table exists so the list can be
    # edited without a redeploy; promoting it to the send path is a separate change,
    # because a half-migrated recipient list is how a briefing gets sent to nobody.
    #
    # Deactivation is a flag, not a DELETE — who used to receive a federal
    # deliverable is part of the delivery record.
    """CREATE TABLE IF NOT EXISTS bulletin_recipients (
         id         TEXT PRIMARY KEY,
         agency_id  TEXT,
         email      TEXT,
         name       TEXT,
         role       TEXT,
         active     BOOLEAN DEFAULT TRUE,
         created_at TEXT,
         updated_at TEXT
       )""",
    # Case-folded so Imran@fcc.gov and imran@fcc.gov cannot both be added and
    # produce a duplicate send.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bulletin_recipients_email "
    "ON bulletin_recipients(agency_id, LOWER(email))",
    "CREATE INDEX IF NOT EXISTS ix_bulletin_recipients_active "
    "ON bulletin_recipients(agency_id, active)",
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


async def save_cost_log(row: Dict[str, Any]) -> bool:
    """Persist one Claude API call's token usage + computed cost (Phase 1).

    Best-effort like every other writer here: returns False rather than raising, so
    a cost-logging problem can never fail a bulletin run.
    """
    if not _enabled:
        return False
    try:
        async with async_session_maker() as s:
            await s.execute(
                text(
                    "INSERT INTO bulletin_cost_logs (id, run_id, agency_id, operation, provider, "
                    "model, tokens_in, tokens_out, api_calls, cost_usd, created_at) "
                    "VALUES (:id, :run_id, :agency_id, :operation, :provider, :model, "
                    ":tokens_in, :tokens_out, :api_calls, :cost_usd, :created_at) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {k: row.get(k) for k in (
                    "id", "run_id", "agency_id", "operation", "provider", "model",
                    "tokens_in", "tokens_out", "api_calls", "cost_usd", "created_at")},
            )
            await s.commit()
        return True
    except Exception as e:
        logger.warning(f"save_cost_log failed: {e}")
        return False


async def fetch_cost_summary(agency_id: str = None, days: int = 30) -> Dict[str, Any]:
    """Aggregate cost rows for GET /api/v1/bulletin/costs. Read-only."""
    if not _enabled:
        return {"enabled": False, "reason": "bulletin store not initialised"}
    try:
        where = "WHERE created_at >= :since"
        params: Dict[str, Any] = {
            "since": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        }
        if agency_id:
            where += " AND agency_id = :agency_id"
            params["agency_id"] = agency_id

        async with async_session_maker() as s:
            totals = (await s.execute(text(
                f"SELECT COALESCE(SUM(cost_usd),0) AS cost, COALESCE(SUM(tokens_in),0) AS tin, "
                f"COALESCE(SUM(tokens_out),0) AS tout, COALESCE(SUM(api_calls),0) AS calls, "
                f"COUNT(DISTINCT run_id) AS runs FROM bulletin_cost_logs {where}"
            ), params)).mappings().first()

            by_op = (await s.execute(text(
                f"SELECT operation, COALESCE(SUM(cost_usd),0) AS cost, "
                f"COALESCE(SUM(api_calls),0) AS calls FROM bulletin_cost_logs {where} "
                f"GROUP BY operation ORDER BY cost DESC"
            ), params)).mappings().all()

            by_run = (await s.execute(text(
                f"SELECT run_id, agency_id, COALESCE(SUM(cost_usd),0) AS cost, "
                f"COALESCE(SUM(api_calls),0) AS calls, MIN(created_at) AS started "
                f"FROM bulletin_cost_logs {where} GROUP BY run_id, agency_id "
                f"ORDER BY started DESC LIMIT 20"
            ), params)).mappings().all()

        runs = int(totals["runs"] or 0)
        total_cost = float(totals["cost"] or 0.0)
        return {
            "enabled": True,
            "window_days": days,
            "agency_id": agency_id,
            "totals": {
                "cost_usd": round(total_cost, 6),
                "tokens_in": int(totals["tin"] or 0),
                "tokens_out": int(totals["tout"] or 0),
                "api_calls": int(totals["calls"] or 0),
                "runs": runs,
                "avg_cost_per_run": round(total_cost / runs, 6) if runs else None,
            },
            "by_operation": [dict(r) for r in by_op],
            "recent_runs": [dict(r) for r in by_run],
        }
    except Exception as e:
        logger.warning(f"fetch_cost_summary failed: {e}")
        return {"enabled": False, "error": str(e)[:200]}


async def fetch_search_profiles(agency_id: str = "fcc", enabled_only: bool = True) -> List[Dict[str, Any]]:
    """Read Boolean search profiles. Returns [] when the store is unavailable or the
    table is empty — callers treat that as 'use the hardcoded fallback'."""
    if not _enabled:
        return []
    try:
        sql = ("SELECT id, agency_id, profile_key, name, boolean_query, description, "
               "enabled, priority FROM bulletin_search_profiles WHERE agency_id = :aid")
        if enabled_only:
            sql += " AND enabled = TRUE"
        sql += " ORDER BY priority ASC, profile_key ASC"
        async with async_session_maker() as s:
            rows = (await s.execute(text(sql), {"aid": agency_id})).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"fetch_search_profiles failed: {e}")
        return []


async def seed_search_profiles(rows: List[Dict[str, Any]]) -> int:
    """Insert seed profiles. Idempotent: existing (agency_id, profile_key) rows are
    left untouched, so an operator's edits are never overwritten by a redeploy."""
    if not _enabled or not rows:
        return 0
    n = 0
    try:
        async with async_session_maker() as s:
            for r in rows:
                res = await s.execute(
                    text(
                        "INSERT INTO bulletin_search_profiles (id, agency_id, profile_key, name, "
                        "boolean_query, description, enabled, priority, created_at, updated_at) "
                        "VALUES (:id, :agency_id, :profile_key, :name, :boolean_query, :description, "
                        ":enabled, :priority, :created_at, :updated_at) "
                        "ON CONFLICT (agency_id, profile_key) DO NOTHING"
                    ),
                    r,
                )
                n += int(res.rowcount or 0)
            await s.commit()
        return n
    except Exception as e:
        logger.warning(f"seed_search_profiles failed: {e}")
        return 0


# ── Recipients (Task 3.5) ─────────────────────────────────────────────────────

async def fetch_recipients(agency_id: str = "fcc",
                           active_only: bool = True) -> List[Dict[str, Any]]:
    """Read the distribution list. [] when the store is unavailable.

    The caller cannot distinguish "no recipients" from "database down" by the
    return value alone — so no send path may treat [] as an authoritative empty
    list. Check store_enabled() first.
    """
    if not _enabled:
        return []
    try:
        sql = ("SELECT id, agency_id, email, name, role, active, created_at, updated_at "
               "FROM bulletin_recipients WHERE agency_id = :aid")
        if active_only:
            sql += " AND active = TRUE"
        sql += " ORDER BY LOWER(email) ASC"
        async with async_session_maker() as s:
            rows = (await s.execute(text(sql), {"aid": agency_id})).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"fetch_recipients failed: {e}")
        return []


async def upsert_recipient(row: Dict[str, Any]) -> str:
    """Add or update one recipient, keyed on (agency_id, lower(email)).

    Re-adding an address that was deactivated reactivates it rather than
    failing on the unique index — from the operator's side "add this person
    back" and "add this person" are the same action.

    Returns "inserted" | "updated" | "unavailable" | "error".
    """
    if not _enabled:
        return "unavailable"
    try:
        async with async_session_maker() as s:
            res = await s.execute(
                text(
                    "INSERT INTO bulletin_recipients (id, agency_id, email, name, role, "
                    "active, created_at, updated_at) "
                    "VALUES (:id, :agency_id, :email, :name, :role, :active, "
                    ":created_at, :updated_at) "
                    "ON CONFLICT (agency_id, (LOWER(email))) DO UPDATE SET "
                    "name = EXCLUDED.name, role = EXCLUDED.role, "
                    "active = EXCLUDED.active, updated_at = EXCLUDED.updated_at "
                    "RETURNING (xmax = 0) AS inserted"
                ),
                row,
            )
            inserted = res.scalar()
            await s.commit()
        return "inserted" if inserted else "updated"
    except Exception as e:
        logger.warning(f"upsert_recipient failed: {e}")
        return "error"


async def deactivate_recipient(agency_id: str, email: str) -> bool:
    """Flag a recipient inactive. Never deletes — see the DDL comment."""
    if not _enabled:
        return False
    try:
        async with async_session_maker() as s:
            res = await s.execute(
                text("UPDATE bulletin_recipients SET active = FALSE, updated_at = :ts "
                     "WHERE agency_id = :aid AND LOWER(email) = LOWER(:email)"),
                {"aid": agency_id, "email": email,
                 "ts": datetime.now(timezone.utc).isoformat()},
            )
            await s.commit()
        return int(res.rowcount or 0) > 0
    except Exception as e:
        logger.warning(f"deactivate_recipient failed: {e}")
        return False


def store_enabled() -> bool:
    """Whether persistence is actually available, so callers can tell an empty
    result apart from an unreachable database."""
    return _enabled


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


# ── Phase 6: expected-source registry (Coverage % denominator) ───────────────
async def save_source_registry(rows: List[Dict[str, Any]]) -> int:
    if not _enabled or not rows:
        return 0
    n = 0
    try:
        async with async_session_maker() as s:
            for r in rows:
                await s.execute(
                    text("INSERT INTO bulletin_source_registry (source_id, name, type, tier, importance_weight, "
                         "enabled, method, url, notes) VALUES (:source_id, :name, :type, :tier, :importance_weight, "
                         ":enabled, :method, :url, :notes) ON CONFLICT (source_id) DO UPDATE SET name=EXCLUDED.name, "
                         "type=EXCLUDED.type, tier=EXCLUDED.tier, importance_weight=EXCLUDED.importance_weight, "
                         "enabled=EXCLUDED.enabled, method=EXCLUDED.method, url=EXCLUDED.url, notes=EXCLUDED.notes"),
                    {"source_id": r.get("source_id"), "name": r.get("name"), "type": r.get("type"),
                     "tier": r.get("tier"), "importance_weight": r.get("importance_weight"),
                     "enabled": r.get("enabled", True), "method": r.get("method"), "url": r.get("url"),
                     "notes": r.get("notes")},
                )
                n += 1
            await s.commit()
    except Exception as e:
        logger.warning(f"save_source_registry failed: {e}")
        return 0
    return n


async def load_source_registry() -> List[Dict[str, Any]]:
    if not _enabled:
        return []
    try:
        async with async_session_maker() as s:
            rows = (await s.execute(text(
                "SELECT source_id, name, type, tier, importance_weight, enabled, method, url, notes "
                "FROM bulletin_source_registry ORDER BY name"))).fetchall()
        cols = ["source_id", "name", "type", "tier", "importance_weight", "enabled", "method", "url", "notes"]
        return [{c: r[i] for i, c in enumerate(cols)} for r in rows]
    except Exception as e:
        logger.warning(f"load_source_registry failed: {e}")
        return []
