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

def _get_engine():
    global _engine
    if _engine is None:
        raw = os.getenv("DATABASE_URL", "")
        db_url = _normalize_url(raw)
        logger.info(f"Creating DB engine: {db_url[:35]}...")
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