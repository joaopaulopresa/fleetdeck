"""Alembic environment: runs migrations over the async engine."""

import asyncio

from alembic import context
from fleetdeck_control import models  # noqa: F401  (registers the tables)
from fleetdeck_control.db import Base
from fleetdeck_control.settings import get_settings
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

target_metadata = Base.metadata


#: Any two processes that migrate at the same time (control plane, seed job) queue on this lock.
_MIGRATION_LOCK = 4_815_162_342


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        connection.execute(text("select pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK})
        context.run_migrations()


async def _main() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
