"""The activator wakes sleeping agents and holds the caller until they answer.

Three things wake an agent: its person opening the chat, a request from another agent, and a
scheduled job. All of them go through :meth:`Activator.ensure_awake`.
"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime

from fleetdeck_common.errors import AgentUnavailableError, ConflictError

from fleetdeck_control import audit, metrics
from fleetdeck_control.agents.service import AgentService
from fleetdeck_control.cluster import AgentCluster
from fleetdeck_control.db import Database
from fleetdeck_control.hermes import HermesClient
from fleetdeck_control.settings import Settings

log = logging.getLogger(__name__)


class Activator:
    """Scales agents from zero and tracks their activity."""

    def __init__(
        self,
        db: Database,
        cluster: AgentCluster,
        hermes: HermesClient,
        agents: AgentService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._cluster = cluster
        self._hermes = hermes
        self._agents = agents
        self._settings = settings
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._busy: defaultdict[str, int] = defaultdict(int)

    def mark_busy(self, agent_id: str, busy: bool) -> None:
        """Count work in progress so the reaper never sleeps an agent in the middle of a run."""
        self._busy[agent_id] += 1 if busy else -1

    def is_busy(self, agent_id: str) -> bool:
        """Return whether the agent has work in progress."""
        return self._busy[agent_id] > 0

    async def ensure_awake(self, agent_id: str, *, trigger: str) -> float:
        """Make sure the agent answers, waking it if needed.

        Returns the seconds spent waiting (0.0 when it was already awake). Raises
        :class:`AgentUnavailableError` if it does not answer within the wake timeout and
        :class:`ConflictError` if it is suspended.
        """
        async with self._locks[agent_id]:
            async with self._db.session() as session:
                agent = await self._agents.get(session, agent_id)
                if agent.suspended:
                    raise ConflictError(f"agent {agent_id} is suspended")
            if await self._hermes.healthy(agent_id):
                await self._set_state(agent_id, "active", touch=True)
                return 0.0

            started = time.monotonic()
            await self._set_state(agent_id, "waking")
            await self._cluster.set_awake(agent_id, True)
            deadline = started + self._settings.wake_timeout_seconds
            while time.monotonic() < deadline:
                if await self._hermes.healthy(agent_id):
                    waited = time.monotonic() - started
                    await self._record_wake(agent_id, trigger, waited)
                    return waited
                await asyncio.sleep(self._settings.wake_poll_seconds)
            await self._set_state(agent_id, "error")
            raise AgentUnavailableError(
                f"agent {agent_id} did not answer within {self._settings.wake_timeout_seconds}s"
            )

    async def touch(self, agent_id: str) -> None:
        """Record activity so the idle timer restarts."""
        await self._set_state(agent_id, None, touch=True)

    async def sleep(self, agent_id: str, *, reason: str) -> None:
        """Scale the agent to zero. Its state stays on the volume."""
        async with self._locks[agent_id]:
            await self._cluster.set_awake(agent_id, False)
            async with self._db.session() as session:
                agent = await self._agents.get(session, agent_id)
                if not agent.suspended:
                    agent.state = "asleep"
                await audit.record(
                    session,
                    kind="agent",
                    action="agent.slept",
                    agent_id=agent_id,
                    person_id=agent.person_id,
                    summary=f"Scaled to zero ({reason})",
                )
        log.info("agent %s asleep (%s)", agent_id, reason)

    async def _record_wake(self, agent_id: str, trigger: str, waited: float) -> None:
        metrics.WAKES.labels(agent=agent_id, trigger=trigger).inc()
        metrics.WAKE_SECONDS.observe(waited)
        async with self._db.session() as session:
            agent = await self._agents.get(session, agent_id)
            agent.state = "active"
            agent.last_activity_at = datetime.now(UTC)
            agent.last_wake_seconds = round(waited, 2)
            agent.wake_count += 1
            await audit.record(
                session,
                kind="agent",
                action="agent.woke",
                agent_id=agent_id,
                person_id=agent.person_id,
                summary=f"Woke in {waited:.1f}s ({trigger})",
                detail={"trigger": trigger, "seconds": round(waited, 2)},
            )
        log.info("agent %s awake after %.1fs (%s)", agent_id, waited, trigger)

    async def _set_state(self, agent_id: str, state: str | None, *, touch: bool = False) -> None:
        async with self._db.session() as session:
            agent = await self._agents.get(session, agent_id)
            if state is not None:
                agent.state = state
            if touch:
                agent.last_activity_at = datetime.now(UTC)
