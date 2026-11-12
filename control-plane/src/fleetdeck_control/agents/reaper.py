"""The idle reaper: puts agents to sleep when nobody has used them for a while."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from fleetdeck_control import metrics
from fleetdeck_control.agents.activator import Activator
from fleetdeck_control.agents.service import AgentService
from fleetdeck_control.db import Database
from fleetdeck_control.models import Agent
from fleetdeck_control.settings import Settings

log = logging.getLogger(__name__)


class IdleReaper:
    """Periodically scales idle agents to zero."""

    def __init__(
        self, db: Database, activator: Activator, agents: AgentService, settings: Settings
    ) -> None:
        self._db = db
        self._activator = activator
        self._agents = agents
        self._settings = settings

    async def reap_once(self, now: datetime | None = None) -> list[str]:
        """Sleep every active agent idle for longer than the limit; return their ids."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._settings.idle_seconds)
        async with self._db.session() as session:
            idle = (
                await session.scalars(
                    select(Agent.id).where(
                        Agent.state == "active",
                        or_(Agent.last_activity_at.is_(None), Agent.last_activity_at < cutoff),
                    )
                )
            ).all()
        reaped: list[str] = []
        for agent_id in idle:
            if self._activator.is_busy(agent_id):
                continue
            await self._activator.sleep(agent_id, reason="idle")
            metrics.IDLE_REAPS.inc()
            reaped.append(agent_id)
        return reaped

    async def run_forever(self) -> None:
        """Run :meth:`reap_once` on an interval until cancelled."""
        while True:
            try:
                await self.reap_once()
                await self._agents.refresh_state_gauge()
            except Exception:
                log.exception("idle reaper pass failed")
            await asyncio.sleep(self._settings.reaper_interval_seconds)
