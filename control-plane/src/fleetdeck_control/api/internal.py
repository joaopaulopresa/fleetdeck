"""The internal API used by the model gateway, the connectors and the egress proxy.

Protected by the internal token and, in the cluster, by network policy.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fleetdeck_common.errors import AuthenticationError
from fleetdeck_common.identity import Identity, RunContext
from fleetdeck_common.tokens import hash_token
from pydantic import BaseModel
from sqlalchemy import select

from fleetdeck_control import audit
from fleetdeck_control.auth import Context, internal_caller
from fleetdeck_control.models import (
    DATA_CLASSES,
    Agent,
    AgentSession,
    ModelCall,
    Person,
    Run,
    most_sensitive,
)

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(internal_caller)])


class ResolveIn(BaseModel):
    """A token presented by an agent, and which of its two tokens it should be."""

    token: str
    kind: Annotated[str, "model | connector"]


class ResolveOut(BaseModel):
    """Who the token belongs to and what that agent is working on."""

    identity: Identity
    context: RunContext


class ModelCallIn(BaseModel):
    """A finished model call, reported by the gateway."""

    agent_id: str
    run_id: str | None = None
    route: str
    data_class: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: int = 200


class AuditIn(BaseModel):
    """An audit record written on behalf of a platform service."""

    kind: str
    action: str
    outcome: str = "info"
    agent_id: str | None = None
    case_id: str | None = None
    system: str | None = None
    summary: str = ""
    detail: dict[str, Any] = {}


class RaiseDataClassIn(BaseModel):
    """A connector returned data of this class into the agent's current session."""

    agent_id: str
    data_class: str
    system: str


@router.post("/identity", response_model=ResolveOut)
async def resolve_identity(body: ResolveIn, context: Context) -> ResolveOut:
    """Resolve an agent token. Unknown tokens and suspended agents are refused."""
    column = Agent.model_key_hash if body.kind == "model" else Agent.connector_token_hash
    async with context.db.session() as session:
        agent = await session.scalar(select(Agent).where(column == hash_token(body.token)))
        if agent is None or agent.suspended:
            raise AuthenticationError("unknown or suspended agent")
        person = await session.get(Person, agent.person_id) if agent.person_id else None
        run = await session.get(Run, agent.current_run_id) if agent.current_run_id else None
        session_row = await session.get(AgentSession, run.session_id) if run else None
    identity = Identity(
        agent_id=agent.id,
        person_id=agent.person_id,
        person_name=person.name if person else None,
        role=person.role if person else "service",
        department=person.department if person else None,
    )
    run_context = RunContext(
        run_id=run.id if run else None,
        session_id=run.session_id if run else None,
        case_id=session_row.case_id if session_row else None,
        data_class=session_row.data_class if session_row else "general",
        trigger=run.trigger if run else None,
        trace_id=run.trace_id if run else None,
    )
    return ResolveOut(identity=identity, context=run_context)


@router.post("/model-calls", status_code=status.HTTP_204_NO_CONTENT)
async def record_model_call(body: ModelCallIn, context: Context) -> None:
    """Store a model call with its attribution and add it to the audit trail."""
    async with context.db.session() as session:
        agent = await session.get(Agent, body.agent_id)
        person = await session.get(Person, agent.person_id) if agent and agent.person_id else None
        run = await session.get(Run, body.run_id) if body.run_id else None
        session_row = await session.get(AgentSession, run.session_id) if run else None
        call = ModelCall(
            agent_id=body.agent_id,
            person_id=person.id if person else None,
            department=person.department if person else None,
            run_id=body.run_id,
            session_id=run.session_id if run else None,
            case_id=session_row.case_id if session_row else None,
            trigger=run.trigger if run else None,
            data_class=body.data_class,
            route=body.route,
            model=body.model,
            input_tokens=body.input_tokens,
            output_tokens=body.output_tokens,
            cached_tokens=body.cached_tokens,
            cost_usd=body.cost_usd,
            latency_ms=body.latency_ms,
            status=body.status,
            trace_id=run.trace_id if run else None,
        )
        session.add(call)
        await audit.record(
            session,
            kind="model_call",
            action=body.model,
            outcome="allowed" if body.status < 400 else "denied",
            person_id=call.person_id,
            agent_id=body.agent_id,
            case_id=call.case_id,
            system="model-gateway",
            run_id=body.run_id,
            trace_id=call.trace_id,
            summary=(
                f"{body.route} route, {body.data_class} data: {body.input_tokens} in, "
                f"{body.output_tokens} out, ${body.cost_usd:.6f}"
            ),
            detail=body.model_dump(),
        )


@router.post("/audit", status_code=status.HTTP_204_NO_CONTENT)
async def record_audit(body: AuditIn, context: Context) -> None:
    """Append an audit record for a connector or the egress proxy."""
    async with context.db.session() as session:
        agent = await session.get(Agent, body.agent_id) if body.agent_id else None
        run = (
            await session.get(Run, agent.current_run_id) if agent and agent.current_run_id else None
        )
        await audit.record(
            session,
            kind=body.kind,
            action=body.action,
            outcome=body.outcome,
            person_id=agent.person_id if agent else None,
            agent_id=body.agent_id,
            case_id=body.case_id,
            system=body.system,
            run_id=run.id if run else None,
            trace_id=run.trace_id if run else None,
            summary=body.summary,
            detail=body.detail,
        )


@router.post("/sessions/raise-data-class", status_code=status.HTTP_204_NO_CONTENT)
async def raise_data_class(body: RaiseDataClassIn, context: Context) -> None:
    """Raise the data class of the agent's current session. It never goes back down."""
    if body.data_class not in DATA_CLASSES:
        raise ValueError(f"unknown data class {body.data_class!r}")
    async with context.db.session() as session:
        agent = await session.get(Agent, body.agent_id)
        run = (
            await session.get(Run, agent.current_run_id) if agent and agent.current_run_id else None
        )
        session_row = await session.get(AgentSession, run.session_id) if run else None
        if session_row is None or run is None:
            return
        raised = most_sensitive(session_row.data_class, body.data_class)
        if raised != session_row.data_class:
            session_row.data_class = raised
            await audit.record(
                session,
                kind="policy",
                action="session.data_class_raised",
                agent_id=body.agent_id,
                person_id=agent.person_id if agent else None,
                case_id=session_row.case_id,
                system=body.system,
                run_id=run.id,
                trace_id=run.trace_id,
                summary=f"Session now {raised}: {body.system} returned {body.data_class} data",
            )
