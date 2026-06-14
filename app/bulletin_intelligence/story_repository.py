"""
DocuAction Bulletin Intelligence — Story Repository Layer
Deliverables: Story Repository Layer, Master Story File, Archive Optimization.

Problem solved: the engine stored articles/briefings in plain in-memory dicts
(`_articles`, `_briefings`). Those reset on every Railway restart/redeploy and
grow unbounded — breaking the 12-month archive requirement and leaking memory.

This repository persists to SQLite (file-backed, zero external service) with:
  - upsert by stable article_id (idempotent ingestion)
  - 12-month retention pruning (Archive Optimization)
  - indexed queries by agency / date / section (Master Story File)
  - graceful degradation: if disk is unavailable, falls back to in-memory so
    the daily cycle never crashes (survives storage outages).

It is intentionally dependency-free (stdlib sqlite3) and lazy-initialized so it
never blocks the FastAPI /health check at import time (same pattern as the
main DB lazy-init lesson).
"""

import os
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default DB path; override with BULLETIN_DB_PATH. /tmp is writable on Railway.
DB_PATH = os.getenv("BULLETIN_DB_PATH", "/tmp/bulletin_stories.db")

_lock = threading.Lock()


class _LazyDB:
    """Lazy SQLite connection. Falls back to in-memory dict if disk fails."""

    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self._mem_articles: Dict[str, Dict[str, Any]] = {}
        self._mem_briefings: Dict[str, Dict[str, Any]] = {}
        self.degraded = False

    def conn(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if self.degraded:
            return None
        try:
            c = sqlite3.connect(self.path, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL;")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    article_id   TEXT PRIMARY KEY,
                    agency_id    TEXT,
                    section      TEXT,
                    outlet       TEXT,
                    title        TEXT,
                    url          TEXT,
                    published_at TEXT,
                    ingested_at  TEXT,
                    data         TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_articles_agency ON articles(agency_id);
                CREATE INDEX IF NOT EXISTS ix_articles_ingested ON articles(ingested_at);
                CREATE INDEX IF NOT EXISTS ix_articles_section ON articles(section);
                CREATE TABLE IF NOT EXISTS briefings (
                    briefing_id   TEXT PRIMARY KEY,
                    agency_id     TEXT,
                    briefing_date TEXT,
                    generated_at  TEXT,
                    data          TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_briefings_agency ON briefings(agency_id);
                """
            )
            c.commit()
            self._conn = c
            logger.info(f"Story repository ready at {self.path}")
            return c
        except Exception as e:
            logger.error(f"Story repository disk init failed ({e}); using in-memory fallback")
            self.degraded = True
            return None


_db = _LazyDB(DB_PATH)


# ── Article persistence ───────────────────────────────────────────────────────
def upsert_article(article: Dict[str, Any]) -> None:
    """Idempotent insert/update by article_id. Accepts a dict (asdict(Article))."""
    aid = article.get("article_id")
    if not aid:
        return
    with _lock:
        c = _db.conn()
        if c is None:
            _db._mem_articles[aid] = article
            return
        try:
            c.execute(
                """INSERT INTO articles
                   (article_id, agency_id, section, outlet, title, url, published_at, ingested_at, data)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(article_id) DO UPDATE SET
                     section=excluded.section, outlet=excluded.outlet,
                     title=excluded.title, url=excluded.url,
                     published_at=excluded.published_at, data=excluded.data""",
                (
                    aid,
                    article.get("agency_id", ""),
                    article.get("topic", "other"),
                    article.get("outlet", ""),
                    article.get("title", ""),
                    article.get("url", ""),
                    article.get("published_at", ""),
                    article.get("ingested_at", _now()),
                    json.dumps(article),
                ),
            )
            c.commit()
        except Exception as e:
            logger.error(f"upsert_article failed: {e}")
            _db._mem_articles[aid] = article


def upsert_articles(articles: List[Dict[str, Any]]) -> int:
    for a in articles:
        upsert_article(a)
    return len(articles)


def get_articles(agency_id: str, limit: int = 500,
                 section: Optional[str] = None,
                 since_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        c = _db.conn()
        if c is None:
            rows = [a for a in _db._mem_articles.values() if a.get("agency_id") == agency_id]
            if section:
                rows = [a for a in rows if a.get("topic") == section]
            return rows[:limit]
        try:
            q = "SELECT data FROM articles WHERE agency_id=?"
            params: List[Any] = [agency_id]
            if section:
                q += " AND section=?"; params.append(section)
            if since_iso:
                q += " AND ingested_at>=?"; params.append(since_iso)
            q += " ORDER BY ingested_at DESC LIMIT ?"; params.append(limit)
            return [json.loads(r[0]) for r in c.execute(q, params).fetchall()]
        except Exception as e:
            logger.error(f"get_articles failed: {e}")
            return []


# ── Briefing persistence (Master Story File) ──────────────────────────────────
def save_briefing(briefing: Dict[str, Any]) -> None:
    bid = briefing.get("briefing_id")
    if not bid:
        return
    with _lock:
        c = _db.conn()
        if c is None:
            _db._mem_briefings[bid] = briefing
            return
        try:
            c.execute(
                """INSERT INTO briefings (briefing_id, agency_id, briefing_date, generated_at, data)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(briefing_id) DO UPDATE SET data=excluded.data""",
                (bid, briefing.get("agency_id", ""), briefing.get("briefing_date", ""),
                 briefing.get("generated_at", _now()), json.dumps(briefing)),
            )
            c.commit()
        except Exception as e:
            logger.error(f"save_briefing failed: {e}")
            _db._mem_briefings[bid] = briefing


def get_briefing(briefing_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        c = _db.conn()
        if c is None:
            return _db._mem_briefings.get(briefing_id)
        try:
            row = c.execute("SELECT data FROM briefings WHERE briefing_id=?",
                            (briefing_id,)).fetchone()
            return json.loads(row[0]) if row else None
        except Exception as e:
            logger.error(f"get_briefing failed: {e}")
            return None


def list_briefings(agency_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with _lock:
        c = _db.conn()
        if c is None:
            return [b for b in _db._mem_briefings.values() if b.get("agency_id") == agency_id][:limit]
        try:
            rows = c.execute(
                "SELECT data FROM briefings WHERE agency_id=? ORDER BY generated_at DESC LIMIT ?",
                (agency_id, limit)).fetchall()
            return [json.loads(r[0]) for r in rows]
        except Exception as e:
            logger.error(f"list_briefings failed: {e}")
            return []


# ── Archive optimization: 12-month retention pruning ──────────────────────────
def prune_old(months: int = 12) -> int:
    """Delete articles older than `months`. Returns count removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat()
    with _lock:
        c = _db.conn()
        if c is None:
            before = len(_db._mem_articles)
            _db._mem_articles = {
                k: v for k, v in _db._mem_articles.items()
                if v.get("ingested_at", "") >= cutoff
            }
            return before - len(_db._mem_articles)
        try:
            cur = c.execute("DELETE FROM articles WHERE ingested_at < ?", (cutoff,))
            c.commit()
            removed = cur.rowcount or 0
            if removed:
                logger.info(f"Archive prune: removed {removed} articles older than {months}mo")
            return removed
        except Exception as e:
            logger.error(f"prune_old failed: {e}")
            return 0


def stats(agency_id: str) -> Dict[str, Any]:
    with _lock:
        c = _db.conn()
        if c is None:
            arts = [a for a in _db._mem_articles.values() if a.get("agency_id") == agency_id]
            return {"total_articles": len(arts), "backend": "memory", "degraded": True}
        try:
            n = c.execute("SELECT COUNT(*) FROM articles WHERE agency_id=?",
                          (agency_id,)).fetchone()[0]
            nb = c.execute("SELECT COUNT(*) FROM briefings WHERE agency_id=?",
                           (agency_id,)).fetchone()[0]
            return {"total_articles": n, "total_briefings": nb,
                    "backend": "sqlite", "degraded": False, "path": _db.path}
        except Exception as e:
            logger.error(f"stats failed: {e}")
            return {"total_articles": 0, "backend": "error"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
