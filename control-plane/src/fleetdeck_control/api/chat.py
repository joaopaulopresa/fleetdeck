"""The employee's conversation with their own agent."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from fleetdeck_common.errors import NotFoundError, PermissionDeniedError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from fleetdeck_control.auth import Context, CurrentPerson
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import Agent, AgentSession, Message, Person, Run

router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    """A message to my agent. Without ``session_id`` a new conversation starts."""

    text: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


class ChatOut(BaseModel):
    """Where to follow the answer."""

    run_id: str
    session_id: str


class SessionOut(BaseModel):
    """A conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    title: str
    case_id: str | None
    data_class: str


class MessageOut(BaseModel):
    """One transcript entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    run_id: str | None


class RunOut(BaseModel):
    """The state of a run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    session_id: str
    trigger: str
    status: str
    output: str | None
    error: str | None
    wake_seconds: float | None
    input_tokens: int
    output_tokens: int


async def own_agent(context: AppContext, person: Person) -> Agent:
    """Return the person's agent or raise :class:`NotFoundError`."""
    async with context.db.session() as session:
        agent = await session.scalar(select(Agent).where(Agent.person_id == person.id))
    if agent is None:
        raise NotFoundError("you have no agent yet")
    return agent


async def _own_session(context: AppContext, agent: Agent, session_id: str) -> AgentSession:
    async with context.db.session() as session:
        row = await session.get(AgentSession, session_id)
    if row is None or row.agent_id != agent.id:
        raise NotFoundError("conversation not found")
    return row


@router.post("/me/chat", response_model=ChatOut)
async def chat(body: ChatIn, context: Context, person: CurrentPerson) -> ChatOut:
    """Send a message to my agent; it is woken if it sleeps."""
    agent = await own_agent(context, person)
    if body.session_id:
        session_row = await _own_session(context, agent, body.session_id)
    else:
        session_row = await context.runs.open_session(agent.id, kind="chat", title=body.text)
    run = await context.runs.submit(agent.id, body.text, trigger="chat", session_id=session_row.id)
    return ChatOut(run_id=run.id, session_id=session_row.id)


@router.get("/me/sessions", response_model=list[SessionOut])
async def my_sessions(context: Context, person: CurrentPerson) -> list[AgentSession]:
    """List my conversations, newest first."""
    agent = await own_agent(context, person)
    async with context.db.session() as session:
        rows = await session.scalars(
            select(AgentSession)
            .where(AgentSession.agent_id == agent.id, AgentSession.kind == "chat")
            .order_by(AgentSession.created_at.desc())
            .limit(50)
        )
        return list(rows.all())


@router.get("/me/sessions/{session_id}/messages", response_model=list[MessageOut])
async def session_messages(
    session_id: str, context: Context, person: CurrentPerson
) -> list[Message]:
    """Return the transcript of one of my conversations."""
    agent = await own_agent(context, person)
    await _own_session(context, agent, session_id)
    async with context.db.session() as session:
        rows = await session.scalars(
            select(Message).where(Message.session_id == session_id).order_by(Message.id)
        )
        return list(rows.all())


async def _visible_run(context: AppContext, person: Person, run_id: str) -> Run:
    async with context.db.session() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise NotFoundError("run not found")
        agent = await session.get(Agent, run.agent_id)
    if not person.is_admin and (agent is None or agent.person_id != person.id):
        raise PermissionDeniedError("not your agent's run")
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, context: Context, person: CurrentPerson) -> Run:
    """Return a run of my agent."""
    return await _visible_run(context, person, run_id)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, context: Context, person: CurrentPerson) -> StreamingResponse:
    """Stream a run's events (server-sent events) from its beginning to its end."""
    run = await _visible_run(context, person, run_id)

    async def body() -> AsyncIterator[str]:
        if run.status in ("completed", "failed"):
            final = {"type": run.status, "output": run.output, "error": run.error}
            yield f"data: {json.dumps(final)}\n\n"
            return
        async for event in context.runs.hub.stream(run_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
