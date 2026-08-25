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

def _safe_dsn_description(url):
    """Describe a connection URL without any part of the credential in it.

    WHY THIS FUNCTION EXISTS
    This line used to log `db_url[:35]`. A normalized URL begins
    `postgresql+asyncpg://` — 21 characters — so after a short username the
    slice runs into the password. With the production username the prefix
    `postgresql+asyncpg://pgadmin:` is 29 characters, and the remaining six
    characters of the slice were the first six characters of the database
    password. Those were emitted at INFO on every engine creation and shipped
    to Application Insights, which is read by a wider audience than the vault.

    A truncated secret is still a secret: it shortens the search space for
    anyone who reads it. So nothing derived from the credential is logged at
    all. Host, database and port identify which server was reached, which is
    the entire operational purpose of the line.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        # Require a parsed hostname before echoing ANY component. Without a
        # netloc, urlparse puts the whole string into `path` — so a malformed
        # DATABASE_URL would be reproduced verbatim in the "db=" field, and a
        # malformed URL can still be carrying a password. Describing it as
        # unparseable is the only safe answer.
        if not parsed.hostname:
            return "unparseable-dsn"
        port = parsed.port or 5432
        database = (parsed.path or "/").lstrip("/") or "unknown-db"
        driver = parsed.scheme or "unknown-driver"
        return f"{driver} host={parsed.hostname} port={port} db={database}"
    except Exception:
        # Never let a description failure break engine creation, and never fall
        # back to printing the URL.
        return "unparseable-dsn"


def _get_engine():
    global _engine
    if _engine is None:
        raw = os.getenv("DATABASE_URL", "")
        db_url = _normalize_url(raw)
        logger.info("Creating DB engine: %s", _safe_dsn_description(db_url))
        _engine = create_async_engine(db_url, echo=False, pool_size=5, max_overflow=10, pool_pre_ping=True)
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