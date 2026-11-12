"""The central scheduler: recurring jobs live here, not inside the agents, so agents can sleep."""

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter
from fleetdeck_common.errors import NotFoundError
from sqlalchemy import select

from fleetdeck_control import audit
from fleetdeck_control.db import Database
from fleetdeck_control.models import Agent, Schedule
from fleetdeck_control.runs import RunService, new_id
from fleetdeck_control.settings import Settings

log = logging.getLogger(__name__)


def next_run(cron: str, after: datetime) -> datetime:
    """Return the first time after ``after`` that the cron expression matches (UTC)."""
    result: datetime = croniter(cron, after).get_next(datetime)
    return result


def validate_cron(cron: str) -> str:
    """Return the expression if it is a valid five-field cron expression."""
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression: {cron!r}")
    return cron


class Scheduler:
    """Stores schedules and starts their runs when due, waking the agent if it sleeps."""

    def __init__(self, db: Database, runs: RunService, settings: Settings) -> None:
        self._db = db
        self._runs = runs
        self._settings = settings

    async def create(
        self, agent_id: str, *, name: str, cron: str, prompt: str, now: datetime | None = None
    ) -> Schedule:
        """Add a schedule for an agent."""
        schedule = Schedule(
            id=new_id("sch"),
            agent_id=agent_id,
            name=name[:120],
            cron=validate_cron(cron),
            prompt=prompt,
            next_run_at=next_run(cron, now or datetime.now(UTC)),
        )
        async with self._db.session() as session:
            session.add(schedule)
        return schedule

    async def run_due(self, now: datetime | None = None) -> list[str]:
        """Start a run for every enabled schedule that is due; return the run ids."""
        now = now or datetime.now(UTC)
        async with self._db.session() as session:
            due = (
                await session.scalars(
                    select(Schedule)
                    .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for schedule in due:
                schedule.next_run_at = next_run(schedule.cron, now)
        return [await self.fire(schedule.id, now=now) for schedule in due]

    async def fire(self, schedule_id: str, *, now: datetime | None = None) -> str:
        """Run a schedule now, in a fresh session of its agent."""
        async with self._db.session() as session:
            schedule = await session.get(Schedule, schedule_id)
            if schedule is None:
                raise NotFoundError("schedule not found")
            agent = await session.get(Agent, schedule.agent_id)
        opened = await self._runs.open_session(
            schedule.agent_id, kind="schedule", title=schedule.name
        )
        run = await self._runs.submit(
            schedule.agent_id,
            schedule.prompt,
            trigger="schedule",
            session_id=opened.id,
            shown_text=f"Scheduled job: {schedule.name}",
        )
        async with self._db.session() as session:
            stored = await session.get(Schedule, schedule_id)
            if stored is not None:
                stored.last_run_at = now or datetime.now(UTC)
                stored.last_run_id = run.id
            await audit.record(
                session,
                kind="schedule",
                action="schedule.fired",
                agent_id=schedule.agent_id,
                person_id=agent.person_id if agent else None,
                run_id=run.id,
                summary=f"Scheduled job started: {schedule.name}",
            )
        return run.id

    async def run_forever(self) -> None:
        """Check for due schedules on an interval until cancelled."""
        while True:
            try:
                await self.run_due()
            except Exception:
                log.exception("scheduler pass failed")
            await asyncio.sleep(self._settings.scheduler_interval_seconds)
