"""Provisioning and lifecycle of agents."""

import logging
from dataclasses import dataclass

from fleetdeck_common.errors import ConflictError, NotFoundError
from fleetdeck_common.naming import validate_agent_id
from fleetdeck_common.tokens import hash_token, new_token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetdeck_control import audit, metrics
from fleetdeck_control.cluster import AgentCluster, AgentKeys, AgentSpec
from fleetdeck_control.db import Database
from fleetdeck_control.models import Agent, Person

log = logging.getLogger(__name__)

STATES = ("provisioning", "asleep", "waking", "active", "suspended", "error")


@dataclass(frozen=True)
class NewAgent:
    """Input for provisioning an agent."""

    agent_id: str
    bundle: str
    person_id: str | None = None
    display_name: str | None = None
    tier: str = "strict"


def spec_for(agent: Agent, person: Person | None, *, awake: bool) -> AgentSpec:
    """Build the cluster spec for a stored agent."""
    return AgentSpec(
        agent_id=agent.id,
        display_name=agent.display_name,
        person_id=agent.person_id,
        person_name=person.name if person else None,
        role=person.role if person else "service",
        department=person.department if person else None,
        bundle=agent.bundle,
        tier=agent.tier,
        awake=awake and not agent.suspended,
        suspended=agent.suspended,
    )


class AgentService:
    """Creates, suspends, resumes and deletes agents."""

    def __init__(self, db: Database, cluster: AgentCluster) -> None:
        self._db = db
        self._cluster = cluster

    async def get(self, session: AsyncSession, agent_id: str) -> Agent:
        """Return the agent or raise :class:`NotFoundError`."""
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise NotFoundError(f"agent {agent_id} not found")
        return agent

    async def create(self, new: NewAgent, *, actor: str | None = None) -> Agent:
        """Provision an agent: keys, database row, custom resource. It starts asleep."""
        validate_agent_id(new.agent_id)
        keys = AgentKeys(
            model_key=new_token("fdm"),
            connector_token=new_token("fdc"),
            runs_key=new_token("fdr"),
        )
        async with self._db.session() as session:
            if await session.get(Agent, new.agent_id) is not None:
                raise ConflictError(f"agent {new.agent_id} already exists")
            person: Person | None = None
            if new.person_id is not None:
                person = await session.get(Person, new.person_id)
                if person is None:
                    raise NotFoundError(f"person {new.person_id} not found")
                taken = await session.scalar(select(Agent).where(Agent.person_id == person.id))
                if taken is not None:
                    raise ConflictError(f"{person.name} already has an agent")
            agent = Agent(
                id=new.agent_id,
                person_id=new.person_id,
                display_name=new.display_name
                or (f"{person.name}'s agent" if person else new.agent_id),
                bundle=new.bundle,
                tier=new.tier,
                state="asleep",
                model_key_hash=hash_token(keys.model_key),
                connector_token_hash=hash_token(keys.connector_token),
            )
            session.add(agent)
            await audit.record(
                session,
                kind="agent",
                action="agent.created",
                person_id=actor,
                agent_id=agent.id,
                summary=f"Agent {agent.id} provisioned with bundle {new.bundle}",
            )
            await session.flush()
            await self._cluster.write_keys(agent.id, keys)
            await self._cluster.apply(spec_for(agent, person, awake=False))
        log.info("agent %s created (bundle %s)", new.agent_id, new.bundle)
        return agent

    async def set_suspended(self, agent_id: str, suspended: bool, *, actor: str | None) -> Agent:
        """Suspend (pod removed, tokens refused) or resume an agent."""
        async with self._db.session() as session:
            agent = await self.get(session, agent_id)
            person = await session.get(Person, agent.person_id) if agent.person_id else None
            agent.suspended = suspended
            agent.state = "suspended" if suspended else "asleep"
            await audit.record(
                session,
                kind="agent",
                action="agent.suspended" if suspended else "agent.resumed",
                person_id=actor,
                agent_id=agent.id,
            )
            await self._cluster.apply(spec_for(agent, person, awake=False))
        return agent

    async def delete(self, agent_id: str, *, actor: str | None) -> None:
        """Delete the agent, its namespace (through the operator) and its keys."""
        async with self._db.session() as session:
            agent = await self.get(session, agent_id)
            await session.delete(agent)
            await audit.record(
                session, kind="agent", action="agent.deleted", person_id=actor, agent_id=agent_id
            )
            await self._cluster.delete(agent_id)
            await self._cluster.delete_keys(agent_id)

    async def refresh_state_gauge(self) -> None:
        """Publish the number of agents in each state as a metric."""
        async with self._db.session() as session:
            rows = await session.execute(select(Agent.state, func.count()).group_by(Agent.state))
            counts = {state: count for state, count in rows.all()}
        for state in STATES:
            metrics.AGENTS_BY_STATE.labels(state=state).set(counts.get(state, 0))
