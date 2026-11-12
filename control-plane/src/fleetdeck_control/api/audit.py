"""Audit search for administrators."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from fleetdeck_control.auth import Context, CurrentAdmin
from fleetdeck_control.models import AuditEvent

router = APIRouter(prefix="/api", tags=["audit"])


class AuditOut(BaseModel):
    """One audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    kind: str
    action: str
    outcome: str
    person_id: str | None
    agent_id: str | None
    case_id: str | None
    system: str | None
    run_id: str | None
    trace_id: str | None
    summary: str
    detail: dict[str, Any]


@router.get("/audit", response_model=list[AuditOut])
async def search_audit(
    context: Context,
    _: CurrentAdmin,
    kind: str | None = None,
    outcome: str | None = None,
    person: str | None = None,
    agent: str | None = None,
    case: str | None = None,
    system: str | None = None,
    run: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEvent]:
    """Search the audit trail by person, agent, case, system, kind, outcome, text and time."""
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    filters = {
        AuditEvent.kind: kind,
        AuditEvent.outcome: outcome,
        AuditEvent.person_id: person,
        AuditEvent.agent_id: agent,
        AuditEvent.case_id: case,
        AuditEvent.system: system,
        AuditEvent.run_id: run,
    }
    for column, value in filters.items():
        if value:
            query = query.where(column == value)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(AuditEvent.summary.ilike(pattern), AuditEvent.action.ilike(pattern))
        )
    if since:
        query = query.where(AuditEvent.at >= since)
    if until:
        query = query.where(AuditEvent.at <= until)
    async with context.db.session() as session:
        return list((await session.scalars(query)).all())
