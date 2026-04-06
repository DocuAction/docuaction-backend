"""
Async PostgreSQL connection via SQLAlchemy
Resilient startup — retries connection instead of crashing.
"""
import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("docuaction.database")

# ═══ DATABASE URL RESOLUTION ═══
# Priority: DATABASE_URL env var → fallback to local
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:docuactions@localhost:5432/railway")

# Normalize URL format for asyncpg
# Railway may provide: postgres://, Postgresql://, postgresql://
# SQLAlchemy asyncpg needs: postgresql+asyncpg://
_original = DB_URL

if DB_URL.lower().startswith("postgres://"):
    # Handle case-insensitive: postgres://, Postgres://, POSTGRES://
    DB_URL = "postgresql+asyncpg://" + DB_URL.split("://", 1)[1]
elif DB_URL.lower().startswith("postgresql://") and "+asyncpg" not in DB_URL.lower():
    # Handle: postgresql://, Postgresql://, PostgreSQL://
    DB_URL = "postgresql+asyncpg://" + DB_URL.split("://", 1)[1]
elif "+asyncpg" not in DB_URL.lower():
    # Fallback: just prepend
    DB_URL = "postgresql+asyncpg://" + DB_URL.split("://", 1)[-1]

logger.info(f"Database URL format: {_original[:15]}... → normalized for asyncpg")

# ═══ ENGINE CREATION ═══
# pool_pre_ping=True: tests connections before using them (handles dropped connections)
# pool_recycle=300: recycle connections every 5 min (prevents stale connections)
engine = create_async_engine(
    DB_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "server_settings": {"application_name": "docuaction-ai"},
        "command_timeout": 30,
    },
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency that provides a database session."""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Test database connectivity without crashing."""
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__('sqlalchemy').text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"Database connection check failed: {e}")
        return False


async def init_database(retries: int = 5, delay: float = 3.0):
    """
    Initialize database tables with retry logic.
    Won't crash the app if DB is temporarily unavailable.
    """
    import asyncio

    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info(f"Database initialized successfully (attempt {attempt})")
            return True
        except Exception as e:
            logger.warning(f"Database init attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 1.5  # exponential backoff
            else:
                logger.error(f"Database initialization failed after {retries} attempts. App will start but DB operations may fail.")
                return False
