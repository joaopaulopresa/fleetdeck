"""Writing audit records. Every service reaches the audit trail through this module."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fleetdeck_control.models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    kind: str,
    action: str,
    outcome: str = "info",
    person_id: str | None = None,
    agent_id: str | None = None,
    case_id: str | None = None,
    system: str | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    summary: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one audit event in the caller's transaction and return it."""
    event = AuditEvent(
        kind=kind,
        action=action,
        outcome=outcome,
        person_id=person_id,
        agent_id=agent_id,
        case_id=case_id,
        system=system,
        run_id=run_id,
        trace_id=trace_id,
        summary=summary,
        detail=detail or {},
    )
    session.add(event)
    await session.flush()
    return event
