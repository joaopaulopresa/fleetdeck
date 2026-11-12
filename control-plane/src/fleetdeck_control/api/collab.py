"""Requests between agents, collaboration policies and schedules."""

from datetime import datetime

from fastapi import APIRouter, status
from fleetdeck_common.bundles import list_bundles
from fleetdeck_common.errors import NotFoundError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from fleetdeck_control import audit
from fleetdeck_control.api.chat import own_agent
from fleetdeck_control.auth import Context, CurrentAdmin, CurrentPerson
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import REQUEST_TYPES, AgentRequest, Person, RequestPolicy, Schedule
from fleetdeck_control.scheduler import next_run, validate_cron

router = APIRouter(prefix="/api", tags=["collaboration"])


class RequestOut(BaseModel):
    """A request as its sender and its recipient see it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    from_person: str | None
    to_person: str | None
    case_id: str
    subject: str
    body: str
    status: str
    reason: str
    reply_body: str | None
    approval: str
    created_at: datetime
    answered_at: datetime | None


class RequestMeta(BaseModel):
    """A request as administrators see it: who, what type, which case, never the content."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    from_person: str | None
    to_person: str | None
    case_id: str
    status: str
    reason: str
    created_at: datetime
    answered_at: datetime | None


class PolicyRow(BaseModel):
    """One rule: this role may send this request type to that role."""

    model_config = ConfigDict(from_attributes=True)

    request_type: str
    from_role: str
    to_role: str
    approval: str = Field(default="required", pattern="^(required|auto)$")
    grants_membership: bool = False
    note: str = ""


class PoliciesOut(BaseModel):
    """The collaboration rules. Whatever is not listed is denied."""

    request_types: list[str]
    rules: list[PolicyRow]


class ScheduleIn(BaseModel):
    """A new recurring job for my agent."""

    name: str = Field(min_length=1, max_length=120)
    cron: str = Field(description="Five-field cron expression, UTC")
    prompt: str = Field(min_length=1, max_length=4000)


class SchedulePatch(BaseModel):
    """Enable or pause a schedule."""

    enabled: bool


class ScheduleOut(BaseModel):
    """A recurring job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    cron: str
    prompt: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: str | None


class BundleOut(BaseModel):
    """A role bundle available for new agents."""

    name: str
    description: str
    connectors: list[str]
    skills: list[str]
    schedules: list[str]
    disabled_toolsets: list[str]


class RunStarted(BaseModel):
    """The run a schedule started."""

    run_id: str


@router.get("/me/requests", response_model=list[RequestOut])
async def my_requests(context: Context, person: CurrentPerson) -> list[AgentRequest]:
    """List the requests my agent sent or received, newest first."""
    return await context.broker.for_person(person.id)


@router.get("/admin/requests", response_model=list[RequestMeta])
async def all_requests(context: Context, _: CurrentAdmin) -> list[AgentRequest]:
    """List request metadata across the company. Subjects and bodies are not included."""
    async with context.db.session() as session:
        rows = await session.scalars(
            select(AgentRequest).order_by(AgentRequest.created_at.desc()).limit(200)
        )
        return list(rows.all())


@router.get("/admin/policies", response_model=PoliciesOut)
async def policies(context: Context, _: CurrentAdmin) -> PoliciesOut:
    """Return who may ask whom for what."""
    async with context.db.session() as session:
        rows = await session.scalars(
            select(RequestPolicy).order_by(RequestPolicy.request_type, RequestPolicy.from_role)
        )
        rules = [PolicyRow.model_validate(row) for row in rows.all()]
    return PoliciesOut(request_types=list(REQUEST_TYPES), rules=rules)


@router.put("/admin/policies", response_model=PoliciesOut)
async def set_policies(rows: list[PolicyRow], context: Context, admin: CurrentAdmin) -> PoliciesOut:
    """Replace the collaboration rules. Takes effect on the next request."""
    for row in rows:
        if row.request_type not in REQUEST_TYPES:
            raise ValueError(f"unknown request type {row.request_type!r}")
    async with context.db.session() as session:
        await session.execute(delete(RequestPolicy))
        session.add_all(RequestPolicy(**row.model_dump()) for row in rows)
        await audit.record(
            session,
            kind="policy",
            action="policies.changed",
            person_id=admin.id,
            system="fleet",
            summary=f"{admin.name} changed the collaboration rules",
            detail={"rules": [row.model_dump() for row in rows]},
        )
    return PoliciesOut(request_types=list(REQUEST_TYPES), rules=rows)


@router.get("/admin/bundles", response_model=list[BundleOut])
async def bundles(context: Context, _: CurrentAdmin) -> list[BundleOut]:
    """List the role bundles an agent can be provisioned with."""
    return [
        BundleOut(
            name=b.name,
            description=b.description,
            connectors=b.connectors,
            skills=[s.name for s in b.skills],
            schedules=[s.name for s in b.schedules],
            disabled_toolsets=b.disabled_toolsets,
        )
        for b in list_bundles(context.settings.bundles_dir)
    ]


async def _own_schedule(context: AppContext, person: Person, schedule_id: str) -> Schedule:
    agent = await own_agent(context, person)
    async with context.db.session() as session:
        schedule = await session.get(Schedule, schedule_id)
    if schedule is None or schedule.agent_id != agent.id:
        raise NotFoundError("schedule not found")
    return schedule


@router.get("/me/schedules", response_model=list[ScheduleOut])
async def my_schedules(context: Context, person: CurrentPerson) -> list[Schedule]:
    """List my agent's recurring jobs."""
    agent = await own_agent(context, person)
    async with context.db.session() as session:
        rows = await session.scalars(
            select(Schedule).where(Schedule.agent_id == agent.id).order_by(Schedule.name)
        )
        return list(rows.all())


@router.post("/me/schedules", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, context: Context, person: CurrentPerson) -> Schedule:
    """Add a recurring job. The central scheduler wakes my agent when it is due."""
    agent = await own_agent(context, person)
    return await context.scheduler.create(
        agent.id, name=body.name, cron=body.cron, prompt=body.prompt
    )


@router.patch("/me/schedules/{schedule_id}", response_model=ScheduleOut)
async def patch_schedule(
    schedule_id: str, body: SchedulePatch, context: Context, person: CurrentPerson
) -> Schedule:
    """Pause or resume one of my schedules."""
    await _own_schedule(context, person, schedule_id)
    async with context.db.session() as session:
        schedule = await session.get(Schedule, schedule_id)
        if schedule is None:
            raise NotFoundError("schedule not found")
        schedule.enabled = body.enabled
        if body.enabled:
            schedule.next_run_at = next_run(
                validate_cron(schedule.cron), datetime.now().astimezone()
            )
    return schedule


@router.delete("/me/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: str, context: Context, person: CurrentPerson) -> None:
    """Remove one of my schedules."""
    await _own_schedule(context, person, schedule_id)
    async with context.db.session() as session:
        await session.execute(delete(Schedule).where(Schedule.id == schedule_id))


@router.post("/me/schedules/{schedule_id}/run", response_model=RunStarted)
async def run_schedule_now(schedule_id: str, context: Context, person: CurrentPerson) -> RunStarted:
    """Run one of my schedules now."""
    await _own_schedule(context, person, schedule_id)
    return RunStarted(run_id=await context.scheduler.fire(schedule_id))
