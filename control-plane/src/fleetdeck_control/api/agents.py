"""Agent endpoints: the fleet view for administrators and "my agent" for employees."""

from fastapi import APIRouter, status
from fleetdeck_common.errors import NotFoundError
from sqlalchemy import select

from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.api.schemas import AgentCreate, AgentOut, WakeOut
from fleetdeck_control.auth import Context, CurrentAdmin, CurrentPerson
from fleetdeck_control.models import Agent

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(context: Context, _: CurrentAdmin) -> list[Agent]:
    """List every agent with its state."""
    async with context.db.session() as session:
        return list((await session.scalars(select(Agent).order_by(Agent.id))).all())


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, context: Context, admin: CurrentAdmin) -> Agent:
    """Provision a new agent. It starts asleep."""
    return await context.agents.create(NewAgent(**body.model_dump()), actor=admin.id)


@router.post("/agents/{agent_id}/suspend", response_model=AgentOut)
async def suspend_agent(agent_id: str, context: Context, admin: CurrentAdmin) -> Agent:
    """Suspend an agent: its pod is removed and its tokens are refused."""
    return await context.agents.set_suspended(agent_id, True, actor=admin.id)


@router.post("/agents/{agent_id}/resume", response_model=AgentOut)
async def resume_agent(agent_id: str, context: Context, admin: CurrentAdmin) -> Agent:
    """Resume a suspended agent."""
    return await context.agents.set_suspended(agent_id, False, actor=admin.id)


@router.post("/agents/{agent_id}/wake", response_model=WakeOut)
async def wake_agent(agent_id: str, context: Context, _: CurrentAdmin) -> WakeOut:
    """Wake an agent and wait until it answers."""
    waited = await context.activator.ensure_awake(agent_id, trigger="admin")
    return WakeOut(agent_id=agent_id, waited_seconds=round(waited, 2))


@router.post("/agents/{agent_id}/sleep", response_model=AgentOut)
async def sleep_agent(agent_id: str, context: Context, _: CurrentAdmin) -> Agent:
    """Scale an agent to zero now."""
    await context.activator.sleep(agent_id, reason="admin")
    async with context.db.session() as session:
        return await context.agents.get(session, agent_id)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, context: Context, admin: CurrentAdmin) -> None:
    """Delete an agent with its namespace, volume and keys."""
    await context.agents.delete(agent_id, actor=admin.id)


@router.get("/me/agent", response_model=AgentOut)
async def my_agent(context: Context, person: CurrentPerson) -> Agent:
    """Return the signed-in person's own agent."""
    async with context.db.session() as session:
        agent = await session.scalar(select(Agent).where(Agent.person_id == person.id))
    if agent is None:
        raise NotFoundError("you have no agent yet")
    return agent


@router.post("/me/agent/wake", response_model=WakeOut)
async def wake_my_agent(context: Context, person: CurrentPerson) -> WakeOut:
    """Wake the signed-in person's agent, as opening the chat does."""
    async with context.db.session() as session:
        agent = await session.scalar(select(Agent).where(Agent.person_id == person.id))
    if agent is None:
        raise NotFoundError("you have no agent yet")
    waited = await context.activator.ensure_awake(agent.id, trigger="chat")
    return WakeOut(agent_id=agent.id, waited_seconds=round(waited, 2))
