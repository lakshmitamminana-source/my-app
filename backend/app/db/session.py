"""Database connection and session management."""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    future=True,
)

async_session_maker = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI routes to get DB session."""
    async with async_session_maker() as session:
        yield session
