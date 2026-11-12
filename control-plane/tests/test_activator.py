from datetime import UTC, datetime, timedelta

import pytest
from fleetdeck_common.errors import AgentUnavailableError
from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.cluster import FakeAgentCluster
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import Agent, AuditEvent
from sqlalchemy import select, text

from .conftest import FakeHermes


async def _agent(context: AppContext, agent_id: str = "elena-park") -> Agent:
    async with context.db.session() as session:
        agent = await session.get(Agent, agent_id)
    assert agent is not None
    return agent


async def test_wake_scales_up_once_and_is_a_no_op_when_already_awake(context: AppContext) -> None:
    await context.agents.create(NewAgent("elena-park", "physician", person_id="elena-park"))
    assert isinstance(context.cluster, FakeAgentCluster)
    assert context.cluster.specs["elena-park"].awake is False

    waited = await context.activator.ensure_awake("elena-park", trigger="chat")
    assert waited > 0
    assert context.cluster.specs["elena-park"].awake is True
    assert await context.activator.ensure_awake("elena-park", trigger="chat") == 0.0
    agent = await _agent(context)
    assert (agent.state, agent.wake_count) == ("active", 1)
    assert agent.last_wake_seconds is not None


async def test_wake_times_out_when_the_pod_never_answers(context: AppContext) -> None:
    await context.agents.create(NewAgent("elena-park", "physician", person_id="elena-park"))
    assert isinstance(context.hermes, FakeHermes)
    context.hermes.never_ready.add("elena-park")
    with pytest.raises(AgentUnavailableError):
        await context.activator.ensure_awake("elena-park", trigger="request")
    assert (await _agent(context)).state == "error"


async def test_reaper_sleeps_idle_agents_but_not_recent_or_busy_ones(context: AppContext) -> None:
    for agent_id in ("elena-park", "marcus-reyes"):
        await context.agents.create(NewAgent(agent_id, "physician", person_id=agent_id))
        await context.activator.ensure_awake(agent_id, trigger="chat")
    later = datetime.now(UTC) + timedelta(seconds=context.settings.idle_seconds + 1)

    assert await context.reaper.reap_once() == []  # nobody is idle yet
    context.activator.mark_busy("marcus-reyes", True)
    assert await context.reaper.reap_once(now=later) == ["elena-park"]
    assert (await _agent(context, "elena-park")).state == "asleep"
    assert (await _agent(context, "marcus-reyes")).state == "active"

    context.activator.mark_busy("marcus-reyes", False)
    assert await context.reaper.reap_once(now=later) == ["marcus-reyes"]
    assert isinstance(context.cluster, FakeAgentCluster)
    assert context.cluster.specs["marcus-reyes"].awake is False


async def test_lifecycle_is_audited_and_the_audit_trail_cannot_be_rewritten(
    context: AppContext,
) -> None:
    await context.agents.create(NewAgent("elena-park", "physician", person_id="elena-park"))
    await context.activator.ensure_awake("elena-park", trigger="chat")
    await context.activator.sleep("elena-park", reason="test")
    async with context.db.session() as session:
        actions = (
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.agent_id == "elena-park")
                .order_by(AuditEvent.id.desc())
                .limit(3)
            )
        ).all()
    assert list(reversed(actions)) == ["agent.created", "agent.woke", "agent.slept"]

    for statement in (
        "update audit_events set summary = 'x'",
        "delete from audit_events",
        "truncate audit_events",
    ):
        with pytest.raises(Exception, match="append-only"):
            async with context.db.session() as session:
                await session.execute(text(statement))
