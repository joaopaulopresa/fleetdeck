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

#: Database role whose queries are subject to row-level security.
SCOPED_ROLE = "fleet_scoped"


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

        With ``person_id`` the transaction runs as the ``fleet_scoped`` database role with
        ``app.person_id`` set, so PostgreSQL row-level security decides which cases, members and
        documents exist for that person. Without it the transaction runs with the service's own
        rights, for platform work that is not done on a person's behalf.
        """
        async with self._sessions() as session, session.begin():
            if person_id is not None:
                await session.execute(
                    text("select set_config('app.person_id', :p, true)"), {"p": person_id}
                )
                await session.execute(text(f"set local role {SCOPED_ROLE}"))
            yield session

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
