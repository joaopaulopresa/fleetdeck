"""Running agents: one run at a time per agent, streamed to whoever watches.

Every way of making an agent work (chat, a request from a colleague's agent, a scheduled job)
goes through :meth:`RunService.submit`. The service wakes the agent, tells the platform what the
agent is working on (so the model gateway can route and attribute its calls), starts the Hermes
run and records what happens.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fleetdeck_common.errors import FleetdeckError, NotFoundError

from fleetdeck_control import audit, metrics
from fleetdeck_control.agents.activator import Activator
from fleetdeck_control.cluster import AgentCluster
from fleetdeck_control.db import Database
from fleetdeck_control.hermes import HermesClient
from fleetdeck_control.models import Agent, AgentSession, Message, Run
from fleetdeck_control.settings import Settings

log = logging.getLogger(__name__)

Event = dict[str, Any]
_FINAL = frozenset({"completed", "failed"})


def new_id(prefix: str) -> str:
    """Return a short unique id such as ``run_3f9a1c...``."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class RunHub:
    """Keeps the events of recent runs in memory so that late watchers can catch up."""

    def __init__(self, keep: int = 200) -> None:
        self._events: dict[str, list[Event]] = {}
        self._changed: defaultdict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
        self._keep = keep

    async def publish(self, run_id: str, event: Event) -> None:
        """Append an event and wake the watchers of that run."""
        if run_id not in self._events and len(self._events) >= self._keep:
            self._events.pop(next(iter(self._events)))
        self._events.setdefault(run_id, []).append(event)
        condition = self._changed[run_id]
        async with condition:
            condition.notify_all()

    async def stream(self, run_id: str) -> AsyncIterator[Event]:
        """Yield the run's events from the beginning until its final event."""
        position = 0
        condition = self._changed[run_id]
        while True:
            events = self._events.get(run_id, [])
            while position < len(events):
                event = events[position]
                position += 1
                yield event
                if event["type"] in _FINAL:
                    return
            async with condition:
                if position >= len(self._events.get(run_id, [])):
                    await condition.wait()


class RunService:
    """Creates, executes and streams runs."""

    def __init__(
        self,
        db: Database,
        activator: Activator,
        hermes: HermesClient,
        cluster: AgentCluster,
        settings: Settings,
    ) -> None:
        self._db = db
        self._activator = activator
        self._hermes = hermes
        self._cluster = cluster
        self._settings = settings
        self.hub = RunHub()
        self._agent_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._runs_keys: dict[str, str] = {}

    async def open_session(
        self,
        agent_id: str,
        *,
        kind: str,
        title: str = "",
        case_id: str | None = None,
        data_class: str = "general",
    ) -> AgentSession:
        """Create a fresh Hermes session for the agent."""
        session_row = AgentSession(
            id=new_id("ses"),
            agent_id=agent_id,
            kind=kind,
            title=title[:200],
            case_id=case_id,
            data_class=data_class,
        )
        async with self._db.session() as session:
            session.add(session_row)
        return session_row

    async def submit(
        self,
        agent_id: str,
        text: str,
        *,
        trigger: str,
        session_id: str,
        instructions: str | None = None,
        shown_text: str | None = None,
    ) -> Run:
        """Queue a run and start executing it in the background.

        ``text`` is what the agent receives; ``shown_text`` (default: the same) is what the
        transcript shows as the user's message.
        """
        run = Run(
            id=new_id("run"),
            agent_id=agent_id,
            session_id=session_id,
            trigger=trigger,
            input=text,
            trace_id=uuid.uuid4().hex,
        )
        async with self._db.session() as session:
            if await session.get(AgentSession, session_id) is None:
                raise NotFoundError("session not found")
            session.add(run)
            session.add(
                Message(
                    session_id=session_id,
                    run_id=run.id,
                    role="user",
                    content=shown_text if shown_text is not None else text,
                )
            )
        task = asyncio.create_task(self._execute(run.id, instructions), name=f"run-{run.id}")
        self._tasks[run.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run.id, None))
        return run

    async def wait(self, run_id: str) -> Run:
        """Wait until the run has finished and return it."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        async with self._db.session() as session:
            run = await session.get(Run, run_id)
        if run is None:
            raise NotFoundError("run not found")
        return run

    async def shutdown(self) -> None:
        """Cancel the runs still in flight (process shutdown)."""
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _runs_key(self, agent_id: str) -> str:
        if agent_id not in self._runs_keys:
            self._runs_keys[agent_id] = (await self._cluster.read_keys(agent_id)).runs_key
        return self._runs_keys[agent_id]

    async def _execute(self, run_id: str, instructions: str | None) -> None:
        async with self._db.session() as session:
            run = await session.get(Run, run_id)
        if run is None:
            return
        agent_id = run.agent_id
        self._activator.mark_busy(agent_id, True)
        try:
            async with self._agent_locks[agent_id]:
                await self._run_on_agent(run, instructions)
        except FleetdeckError as exc:
            await self._fail(run, str(exc))
        except asyncio.CancelledError:
            await self._fail(run, "cancelled")
            raise
        except Exception as exc:
            log.exception("run %s crashed", run_id)
            await self._fail(run, f"internal error: {exc}")
        finally:
            self._activator.mark_busy(agent_id, False)
            async with self._db.session() as session:
                agent = await session.get(Agent, agent_id)
                if agent is not None and agent.current_run_id == run_id:
                    agent.current_run_id = None
                    agent.last_activity_at = datetime.now(UTC)

    async def _run_on_agent(self, run: Run, instructions: str | None) -> None:
        await self._update(run.id, status="waking")
        await self.hub.publish(run.id, {"type": "status", "status": "waking"})
        waited = await self._activator.ensure_awake(run.agent_id, trigger=run.trigger)
        async with self._db.session() as session:
            agent = await session.get(Agent, run.agent_id)
            if agent is None:
                raise NotFoundError("agent not found")
            agent.current_run_id = run.id
        key = await self._runs_key(run.agent_id)
        hermes_run_id = await self._hermes.start_run(
            run.agent_id,
            key,
            text=run.input,
            session_id=run.session_id,
            instructions=instructions,
        )
        await self._update(
            run.id,
            status="running",
            hermes_run_id=hermes_run_id,
            wake_seconds=round(waited, 2),
            started_at=datetime.now(UTC),
        )
        await self.hub.publish(
            run.id, {"type": "status", "status": "running", "wake_seconds": round(waited, 2)}
        )

        output: str | None = None
        usage: dict[str, Any] = {}
        async for event in self._hermes.events(run.agent_id, key, hermes_run_id):
            data = event.data
            if event.event == "message.delta":
                delta = str(data.get("delta") or data.get("text") or "")
                if delta:
                    await self.hub.publish(run.id, {"type": "delta", "text": delta})
            elif event.event in ("tool.started", "tool.completed"):
                await self._tool_event(run, event.event.removeprefix("tool."), data)
            elif event.event == "run.completed":
                output = str(data.get("output") or "")
                usage = dict(data.get("usage") or {})
            elif event.terminal:
                raise FleetdeckError(f"run ended with {event.event}: {data.get('error', '')}")
        if output is None:
            raise FleetdeckError("the agent's event stream ended without a result")

        async with self._db.session() as session:
            session.add(
                Message(session_id=run.session_id, run_id=run.id, role="assistant", content=output)
            )
        await self._update(
            run.id,
            status="completed",
            output=output,
            ended_at=datetime.now(UTC),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
        metrics.RUNS.labels(trigger=run.trigger, status="completed").inc()
        await self.hub.publish(run.id, {"type": "completed", "output": output, "usage": usage})

    async def _tool_event(self, run: Run, phase: str, data: dict[str, Any]) -> None:
        tool = str(data.get("tool") or "")
        event: Event = {
            "type": "tool",
            "phase": phase,
            "tool": tool,
            "error": bool(data.get("error")),
        }
        await self.hub.publish(run.id, event)
        if phase != "completed":
            return
        async with self._db.session() as session:
            session.add(
                Message(
                    session_id=run.session_id,
                    run_id=run.id,
                    role="tool",
                    content=tool,
                    meta={"error": bool(data.get("error"))},
                )
            )
            if not tool.startswith("mcp__"):
                # Connector calls are audited by the connector itself, with the caller it saw.
                agent = await session.get(Agent, run.agent_id)
                await audit.record(
                    session,
                    kind="tool_call",
                    action=tool,
                    agent_id=run.agent_id,
                    person_id=agent.person_id if agent else None,
                    run_id=run.id,
                    trace_id=run.trace_id,
                    system="hermes",
                    outcome="denied" if data.get("error") else "allowed",
                    summary=f"Built-in tool {tool}",
                )

    async def _fail(self, run: Run, error: str) -> None:
        await self._update(run.id, status="failed", error=error, ended_at=datetime.now(UTC))
        metrics.RUNS.labels(trigger=run.trigger, status="failed").inc()
        await self.hub.publish(run.id, {"type": "failed", "error": error})
        log.warning("run %s failed: %s", run.id, error)

    async def _update(self, run_id: str, **values: Any) -> None:
        async with self._db.session() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                for key, value in values.items():
                    setattr(run, key, value)
