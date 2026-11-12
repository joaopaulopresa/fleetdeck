"""Command line of the control plane: ``migrate``, ``seed`` and ``serve``."""

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from fleetdeck_common.logs import configure_logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from fleetdeck_control.settings import get_settings

log = logging.getLogger(__name__)


def alembic_config(database_url: str | None = None) -> Config:
    """Return an Alembic configuration pointing at the packaged migrations."""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url)
    return config


def migrate(database_url: str | None = None) -> None:
    """Apply all migrations."""
    command.upgrade(alembic_config(database_url), "head")


async def ensure_connector_login(database_url: str, password: str) -> None:
    """Create or update ``fleet_connector``: a login that is only a member of ``fleet_scoped``.

    The knowledge connector uses it, so row-level security applies to everything it reads.
    """
    engine = create_async_engine(database_url)
    quoted = password.replace("'", "''")
    async with engine.begin() as connection:
        exists = await connection.scalar(
            text("select 1 from pg_roles where rolname = 'fleet_connector'")
        )
        verb = "alter" if exists else "create"
        await connection.execute(text(f"{verb} role fleet_connector login password '{quoted}'"))
        await connection.execute(text("grant fleet_scoped to fleet_connector"))
    await engine.dispose()


def main() -> None:
    """Entry point of ``fleetdeck-control``."""
    parser = argparse.ArgumentParser(prog="fleetdeck-control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    seed = sub.add_parser("seed", help="load the demo company and provision its agents")
    seed.add_argument("--path", default="/app/seed", help="directory with the seed files")
    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "migrate":
        migrate()
        if settings.connector_db_password:
            asyncio.run(
                ensure_connector_login(settings.database_url, settings.connector_db_password)
            )
    elif args.command == "seed":
        from fleetdeck_control.seed import seed_all

        asyncio.run(seed_all(Path(args.path)))
    else:
        uvicorn.run(
            "fleetdeck_control.main:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_config=None,
        )


if __name__ == "__main__":
    main()
