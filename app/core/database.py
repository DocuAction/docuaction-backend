"""Async PostgreSQL - Lazy initialization, never crashes at import"""
import os
import logging
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

logger = logging.getLogger("docuaction.database")

class Base(DeclarativeBase):
    pass

_engine = None
_session_maker = None

def _normalize_url(url):
    if not url:
        return "postgresql+asyncpg://postgres:postgres@localhost:5432/railway"
    parts = url.split("://", 1)
    if len(parts) != 2:
        return "postgresql+asyncpg://" + url
    rest = parts[1]
    return "postgresql+asyncpg://" + rest

def _ssl_connect_args():
    """Optional transport encryption for the DB connection (NIST SC-8 / SC-13),
    configured by ENV only — no schema/migration/URL change.

    DATABASE_SSL accepts libpq-style modes, passed straight to asyncpg:
      unset / "disable" / "off"  → no change (default; identical to prior behavior)
      "require" (or "true")      → encrypt; no certificate verification (works with
                                    managed Postgres self-signed certs, e.g. Railway)
      "verify-ca" / "verify-full"→ encrypt + verify server certificate (FedRAMP /
                                    NIST SC-8(1); relies on the platform's FIPS-
                                    validated OpenSSL module — FIPS 140-3 ready)

    If DATABASE_URL already encodes sslmode, leave DATABASE_SSL unset and that
    setting continues to govern.
    """
    mode = os.getenv("DATABASE_SSL", "").strip().lower()
    if not mode or mode in ("disable", "false", "off", "0", "none"):
        return {}
    if mode in ("true", "on", "1", "enable", "enabled"):
        mode = "require"
    return {"ssl": mode}


def _get_engine():
    global _engine
    if _engine is None:
        raw = os.getenv("DATABASE_URL", "")
        db_url = _normalize_url(raw)
        connect_args = _ssl_connect_args()
        ssl_desc = connect_args.get("ssl", "off (default)")
        logger.info(f"Creating DB engine: {db_url[:35]}... (SSL={ssl_desc})")
        _engine = create_async_engine(
            db_url, echo=False, pool_size=5, max_overflow=10, pool_pre_ping=True,
            connect_args=connect_args,
        )
    return _engine

def _get_session_maker():
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(_get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_maker

class _EngineProxy:
    def __getattr__(self, name):
        return getattr(_get_engine(), name)
    def begin(self):
        return _get_engine().begin()
    async def dispose(self):
        global _engine
        if _engine:
            await _engine.dispose()
            _engine = None

engine = _EngineProxy()

def async_session_maker():
    return _get_session_maker()()

async def get_db():
    maker = _get_session_maker()
    async with maker() as session:
        try:
            yield session
        finally:
            await session.close()

async def check_db_connection():
    try:
        e = _get_engine()
        async with e.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as ex:
        logger.warning(f"DB check failed: {ex}")
        return False

async def init_database(retries=5, delay=2.0):
    for attempt in range(1, retries + 1):
        try:
            e = _get_engine()
            async with e.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info(f"DB initialized (attempt {attempt})")
            return True
        except Exception as ex:
            logger.warning(f"DB init {attempt}/{retries}: {ex}")
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 1.5
    logger.error("DB init failed. App starts without DB.")
    return False