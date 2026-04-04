"""Database base and initialization."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Convert SQLite URL to async version if needed
database_url = settings.DATABASE_URL
is_sqlite = database_url.startswith("sqlite:///")
if is_sqlite:
    database_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///")

# SQLite-specific configuration for better concurrency
connect_args = {}
if is_sqlite:
    # These settings help with concurrent access
    connect_args = {
        "check_same_thread": False,
        "timeout": 30.0,  # Wait up to 30 seconds for locks
    }

engine = create_async_engine(
    database_url,
    echo=False,
    future=True,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=3600,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if is_sqlite:
            # Enable WAL mode for better concurrency
            try:
                await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
                logger.info("SQLite WAL mode enabled")
            except Exception as e:
                logger.warning(f"Could not enable WAL mode: {e}")
    logger.info("Database initialized")


async def get_db() -> AsyncSession:
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            raise
        finally:
            await session.close()
