"""Deciding whether an agent may use a connector tool.

Connectors do not decide on their own. For every call they send the agent's token, the tool and
the case the record belongs to; this module answers from the role permissions and the case
membership stored in the database, and writes the decision to the audit trail.
"""

from dataclasses import dataclass

from fleetdeck_common.errors import AuthenticationError
from fleetdeck_common.identity import Identity, RunContext
from fleetdeck_common.tokens import hash_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetdeck_control import audit, metrics
from fleetdeck_control.models import (
    Agent,
    AgentSession,
    CaseMember,
    Person,
    Run,
    System,
    ToolPermission,
    most_sensitive,
)


@dataclass(frozen=True)
class Caller:
    """An agent resolved from a token, with what it is working on."""

    agent: Agent
    person: Person | None
    run: Run | None
    session_row: AgentSession | None

    @property
    def identity(self) -> Identity:
        """Return the identity handed to platform services."""
        return Identity(
            agent_id=self.agent.id,
            person_id=self.agent.person_id,
            person_name=self.person.name if self.person else None,
            role=self.person.role if self.person else "service",
            department=self.person.department if self.person else None,
        )

    @property
    def context(self) -> RunContext:
        """Return what the agent is working on."""
        return RunContext(
            run_id=self.run.id if self.run else None,
            session_id=self.run.session_id if self.run else None,
            case_id=self.session_row.case_id if self.session_row else None,
            data_class=self.session_row.data_class if self.session_row else "general",
            trigger=self.run.trigger if self.run else None,
            trace_id=self.run.trace_id if self.run else None,
        )


@dataclass(frozen=True)
class Decision:
    """The answer to "may this agent use this tool on this case?"."""

    allowed: bool
    reason: str
    identity: Identity
    context: RunContext


async def resolve_caller(session: AsyncSession, token: str, kind: str) -> Caller:
    """Resolve a ``model`` key or a ``connector`` token; refuse unknown and suspended agents."""
    column = Agent.model_key_hash if kind == "model" else Agent.connector_token_hash
    agent = await session.scalar(select(Agent).where(column == hash_token(token)))
    if agent is None or agent.suspended:
        raise AuthenticationError("unknown or suspended agent")
    person = await session.get(Person, agent.person_id) if agent.person_id else None
    run = await session.get(Run, agent.current_run_id) if agent.current_run_id else None
    session_row = await session.get(AgentSession, run.session_id) if run else None
    return Caller(agent=agent, person=person, run=run, session_row=session_row)


async def is_member(session: AsyncSession, case_id: str, person_id: str | None) -> bool:
    """Return whether the person is a member of the case."""
    if person_id is None:
        return False
    return await session.get(CaseMember, (case_id, person_id)) is not None


async def authorize_tool(
    session: AsyncSession,
    *,
    token: str,
    system: str,
    tool: str,
    case_id: str | None,
    record: str = "",
) -> Decision:
    """Decide one connector call, audit it and, if allowed, mark the session's data class."""
    caller = await resolve_caller(session, token, "connector")
    identity = caller.identity
    permission = await session.get(ToolPermission, (system, tool, identity.role))
    system_row = await session.get(System, system)

    if permission is None or system_row is None:
        allowed, reason = False, f"role {identity.role} may not use {system}.{tool}"
    elif permission.scope == "case" and not (
        case_id and await is_member(session, case_id, identity.person_id)
    ):
        # The same answer whether the case exists or not: non-members learn nothing.
        allowed, reason = False, "no access to this record: not a member of its case"
    else:
        allowed, reason = True, "ok"

    if allowed and system_row is not None and caller.session_row is not None:
        raised = most_sensitive(caller.session_row.data_class, system_row.data_class)
        if raised != caller.session_row.data_class:
            caller.session_row.data_class = raised
            await audit.record(
                session,
                kind="policy",
                action="session.data_class_raised",
                agent_id=identity.agent_id,
                person_id=identity.person_id,
                case_id=caller.session_row.case_id,
                system=system,
                run_id=caller.run.id if caller.run else None,
                trace_id=caller.run.trace_id if caller.run else None,
                summary=f"Session now {raised}: {system} holds {system_row.data_class} data",
            )
    if not allowed:
        metrics.CONNECTOR_DENIALS.labels(
            system=system, reason="role" if permission is None else "case"
        ).inc()
    await audit.record(
        session,
        kind="tool_call",
        action=f"{system}.{tool}",
        outcome="allowed" if allowed else "denied",
        person_id=identity.person_id,
        agent_id=identity.agent_id,
        case_id=case_id,
        system=system,
        run_id=caller.run.id if caller.run else None,
        trace_id=caller.run.trace_id if caller.run else None,
        summary=(
            f"{identity.person_name or identity.agent_id} ({identity.role}) "
            f"{'used' if allowed else 'was refused'} {system}.{tool}"
            f"{' on ' + record if record else ''}"
            f"{'' if allowed else ': ' + reason}"
        ),
        detail={"record": record, "reason": reason},
    )
    return Decision(allowed=allowed, reason=reason, identity=identity, context=caller.context)
