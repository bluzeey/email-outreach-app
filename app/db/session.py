"""Database session utilities."""

from app.db.base import AsyncSessionLocal


async def get_session():
    """Get a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
