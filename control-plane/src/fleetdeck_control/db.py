"""Database engine and session handling."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base of all control-plane tables."""


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self, person_id: str | None = None) -> AsyncIterator[AsyncSession]:
        """Open a transaction; commit on success, roll back on error.

        ``person_id`` is published to PostgreSQL as ``app.person_id`` for the transaction, which
        is what the row-level security policies read. Without it the policies see no person and
        return no protected rows.
        """
        async with self._sessions() as session, session.begin():
            if person_id is not None:
                await session.execute(
                    text("select set_config('app.person_id', :p, true)"), {"p": person_id}
                )
            yield session

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
