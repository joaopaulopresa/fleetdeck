"""Fixtures: a real PostgreSQL in a container, and the app wired to fake cluster and Hermes."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fleetdeck_control.cli import migrate
from fleetdeck_control.cluster import FakeAgentCluster
from fleetdeck_control.context import AppContext, build_context
from fleetdeck_control.hermes import RunEvent
from fleetdeck_control.main import create_app
from fleetdeck_control.models import Person
from fleetdeck_control.settings import Settings
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

PEOPLE = [
    Person(
        id="elena-park",
        name="Dr. Elena Park",
        email="elena@example.test",
        title="Physician",
        role="physician",
        department="Clinical",
        is_admin=False,
    ),
    Person(
        id="marcus-reyes",
        name="Marcus Reyes",
        email="marcus@example.test",
        title="Medical Biller",
        role="biller",
        department="Revenue Cycle",
        is_admin=False,
    ),
    Person(
        id="priya-nair",
        name="Priya Nair",
        email="priya@example.test",
        title="Litigation Lawyer",
        role="lawyer",
        department="Legal",
        is_admin=False,
    ),
    Person(
        id="dana-whitfield",
        name="Dana Whitfield",
        email="dana@example.test",
        title="Platform Administrator",
        role="platform-admin",
        department="IT",
        is_admin=True,
    ),
]


class FakeHermes:
    """A Hermes that is healthy exactly when the fake cluster says the agent is awake."""

    def __init__(self, cluster: FakeAgentCluster) -> None:
        self._cluster = cluster
        self.never_ready: set[str] = set()
        self.runs: list[dict[str, Any]] = []
        self.script: list[RunEvent] = [
            RunEvent(
                "run.completed",
                {
                    "event": "run.completed",
                    "output": "ok",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            )
        ]

    async def healthy(self, agent_id: str) -> bool:
        spec = self._cluster.specs.get(agent_id)
        return bool(spec and spec.awake and agent_id not in self.never_ready)

    async def start_run(
        self,
        agent_id: str,
        runs_key: str,
        *,
        text: str,
        session_id: str,
        instructions: str | None = None,
    ) -> str:
        self.runs.append(
            {
                "agent_id": agent_id,
                "runs_key": runs_key,
                "text": text,
                "session_id": session_id,
                "instructions": instructions,
            }
        )
        return f"run_{len(self.runs)}"

    async def events(self, agent_id: str, runs_key: str, run_id: str) -> AsyncIterator[RunEvent]:
        for event in self.script:
            yield event


class FakeConfirmer:
    """Records which drafts were confirmed or discarded in the corporate systems."""

    def __init__(self) -> None:
        self.confirmed: list[tuple[str, str, str, str]] = []
        self.discarded: list[tuple[str, str]] = []

    async def confirm(self, system: str, ref: str, *, person_id: str, body: str) -> None:
        self.confirmed.append((system, ref, person_id, body))

    async def discard(self, system: str, ref: str, *, person_id: str) -> None:
        self.discarded.append((system, ref))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        migrate(url)
        yield url


@pytest.fixture
async def context(database_url: str) -> AsyncIterator[AppContext]:
    settings = Settings(
        database_url=database_url,
        cluster_mode="fake",
        background_tasks=False,
        wake_timeout_seconds=1,
        wake_poll_seconds=0.01,
        idle_seconds=60,
        internal_token="internal-test-token",
    )
    cluster = FakeAgentCluster()
    ctx = build_context(
        settings, cluster=cluster, hermes=FakeHermes(cluster), confirmer=FakeConfirmer()
    )
    async with ctx.db.engine.begin() as connection:
        tables = (
            await connection.execute(
                text(
                    "select string_agg(format('%I', tablename), ', ') from pg_tables "
                    "where schemaname = 'public' "
                    "and tablename not in ('alembic_version', 'audit_events')"
                )
            )
        ).scalar_one()
        await connection.execute(text(f"truncate {tables} restart identity cascade"))
    async with ctx.db.session() as session:
        session.add_all(
            Person(**{c.name: getattr(p, c.name) for c in Person.__table__.columns}) for p in PEOPLE
        )
    yield ctx
    await ctx.db.dispose()


@pytest.fixture
async def client(context: AppContext) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(context.settings, context)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def sign_in(client: httpx.AsyncClient, person_id: str) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"person_id": person_id})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
async def admin(client: httpx.AsyncClient) -> dict[str, str]:
    return await sign_in(client, "dana-whitfield")
