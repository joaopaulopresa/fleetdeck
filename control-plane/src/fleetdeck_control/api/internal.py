"""The internal API used by the model gateway, the connectors and the egress proxy.

Protected by the internal token and, in the cluster, by network policy.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from fleetdeck_common.errors import NotFoundError
from fleetdeck_common.identity import Identity, RunContext
from pydantic import BaseModel
from sqlalchemy import select

from fleetdeck_control import audit
from fleetdeck_control.access import authorize_tool, resolve_caller
from fleetdeck_control.auth import Context, internal_caller
from fleetdeck_control.models import (
    DATA_CLASSES,
    Agent,
    AgentSession,
    CaseMember,
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


class AuthorizeIn(BaseModel):
    """A connector asks whether the agent behind ``token`` may use ``tool``."""

    token: str
    system: str
    tool: str
    #: The case the record belongs to, when it belongs to one.
    case_id: str | None = None
    #: A short label of the record, for the audit trail.
    record: str = ""


class AuthorizeOut(BaseModel):
    """The decision, with who asked."""

    allowed: bool
    reason: str
    identity: Identity
    context: RunContext


class ApprovalIn(BaseModel):
    """A draft waiting for its person."""

    agent_id: str
    system: str
    ref: str
    title: str
    body: str = ""
    case_id: str | None = None
    detail: dict[str, Any] = {}


class ApprovalCreated(BaseModel):
    """The id of the new approval."""

    id: str


class RaiseDataClassIn(BaseModel):
    """A connector returned data of this class into the agent's current session."""

    agent_id: str
    data_class: str
    system: str


@router.post("/identity", response_model=ResolveOut)
async def resolve_identity(body: ResolveIn, context: Context) -> ResolveOut:
    """Resolve an agent token. Unknown tokens and suspended agents are refused."""
    async with context.db.session() as session:
        caller = await resolve_caller(session, body.token, body.kind)
        return ResolveOut(identity=caller.identity, context=caller.context)


@router.post("/authorize", response_model=AuthorizeOut)
async def authorize(body: AuthorizeIn, context: Context) -> AuthorizeOut:
    """Decide and audit one connector call: role permission, then case membership."""
    async with context.db.session() as session:
        decision = await authorize_tool(
            session,
            token=body.token,
            system=body.system,
            tool=body.tool,
            case_id=body.case_id,
            record=body.record,
        )
    return AuthorizeOut(
        allowed=decision.allowed,
        reason=decision.reason,
        identity=decision.identity,
        context=decision.context,
    )


@router.post("/member-cases", response_model=list[str])
async def member_cases(body: ResolveIn, context: Context) -> list[str]:
    """Return the ids of the cases the agent's person is a member of."""
    async with context.db.session() as session:
        caller = await resolve_caller(session, body.token, body.kind)
        if caller.agent.person_id is None:
            return []
        rows = await session.scalars(
            select(CaseMember.case_id).where(CaseMember.person_id == caller.agent.person_id)
        )
        return sorted(rows.all())


@router.post("/approvals", response_model=ApprovalCreated)
async def create_approval(body: ApprovalIn, context: Context) -> ApprovalCreated:
    """Register a draft that an agent prepared in a system and its person must confirm."""
    async with context.db.session() as session:
        agent = await session.get(Agent, body.agent_id)
        if agent is None or agent.person_id is None:
            raise NotFoundError("agent has no person to confirm drafts")
        approval = await context.approvals.create(
            session,
            kind="draft",
            person_id=agent.person_id,
            agent_id=agent.id,
            case_id=body.case_id,
            system=body.system,
            ref=body.ref,
            title=body.title,
            body=body.body,
            detail=body.detail,
        )
    return ApprovalCreated(id=approval.id)


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
