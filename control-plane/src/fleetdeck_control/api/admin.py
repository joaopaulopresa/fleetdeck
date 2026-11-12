"""Console endpoints: people, usage and cost, capacity, settings, skills and demo controls."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter
from fleetdeck_common.errors import UpstreamError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Date, cast, delete, func, select, update

from fleetdeck_control import audit, capacity
from fleetdeck_control.api.schemas import PersonOut
from fleetdeck_control.auth import Context, CurrentAdmin, CurrentPerson
from fleetdeck_control.models import (
    Agent,
    AgentRequest,
    AgentSession,
    Approval,
    AuditEvent,
    Case,
    KbDocument,
    ModelCall,
    Person,
    Setting,
    SkillProposal,
)
from fleetdeck_control.seed import _load, seed_cases, seed_knowledge

router = APIRouter(prefix="/api", tags=["console"])


class ConfigOut(BaseModel):
    """What the web app needs to know about this installation."""

    company: str
    langfuse_trace_url: str
    grafana_url: str
    default_model: str
    restricted_model: str
    idle_seconds: int


class AdminPerson(PersonOut):
    """A person with the id of their agent, if they have one."""

    agent_id: str | None


class SettingsBody(BaseModel):
    """Runtime settings of the fleet."""

    idle_seconds: int = Field(ge=20, le=86_400)


class SkillOut(BaseModel):
    """A skill or a proposal."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    body: str
    scope: str
    owner_person_id: str | None = None
    roles: list[str] = []
    status: str
    created_at: datetime | None = None


class SkillDecision(BaseModel):
    """For shared skills: the roles whose agents receive it."""

    roles: list[str] | None = None


class AppealDecisionIn(BaseModel):
    """The payer's answer to an open appeal. The payer is simulated in the demo."""

    claim_id: str
    outcome: str = Field(default="upheld", pattern="^(upheld|overturned)$")


@router.get("/config", response_model=ConfigOut)
async def config(context: Context, _: CurrentPerson) -> ConfigOut:
    """Return installation facts for the web app."""
    settings = context.settings
    return ConfigOut(
        company=settings.company_name,
        langfuse_trace_url=settings.langfuse_trace_url,
        grafana_url=settings.grafana_url,
        default_model=settings.default_model,
        restricted_model=settings.restricted_model,
        idle_seconds=await context.reaper.idle_seconds(),
    )


@router.get("/admin/people", response_model=list[AdminPerson])
async def people(context: Context, _: CurrentAdmin) -> list[AdminPerson]:
    """List everyone, with their agent if they have one."""
    async with context.db.session() as session:
        rows = (
            await session.execute(
                select(Person, Agent.id)
                .outerjoin(Agent, Agent.person_id == Person.id)
                .order_by(Person.name)
            )
        ).all()
    return [
        AdminPerson(**PersonOut.model_validate(p).model_dump(), agent_id=agent_id)
        for p, agent_id in rows
    ]


@router.get("/admin/usage")
async def usage(context: Context, _: CurrentAdmin, days: int = 30) -> dict[str, Any]:
    """Return model usage and cost by person, department, model, task type and data class."""
    since = datetime.now(UTC) - timedelta(days=days)
    measures = (
        func.count().label("calls"),
        func.coalesce(func.sum(ModelCall.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(ModelCall.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(ModelCall.cost_usd), 0.0).label("cost_usd"),
    )

    async with context.db.session() as session:

        async def grouped(*columns: Any) -> list[dict[str, Any]]:
            rows = await session.execute(
                select(*columns, *measures)
                .where(ModelCall.at >= since)
                .group_by(*columns)
                .order_by(func.sum(ModelCall.cost_usd).desc())
            )
            return [dict(row._mapping) for row in rows.all()]

        names = {p.id: p.name for p in (await session.scalars(select(Person))).all()}
        by_person = await grouped(ModelCall.person_id, ModelCall.department)
        day = cast(ModelCall.at, Date).label("day")
        daily = [
            dict(row._mapping)
            for row in (
                await session.execute(
                    select(day, *measures).where(ModelCall.at >= since).group_by(day).order_by(day)
                )
            ).all()
        ]
        totals = (await session.execute(select(*measures).where(ModelCall.at >= since))).one()
        return {
            "days": days,
            "totals": dict(totals._mapping),
            "by_person": [
                {**row, "name": names.get(row["person_id"], row["person_id"])} for row in by_person
            ],
            "by_department": await grouped(ModelCall.department),
            "by_model": await grouped(ModelCall.model, ModelCall.route),
            "by_task_type": await grouped(ModelCall.trigger),
            "by_data_class": await grouped(ModelCall.data_class),
            "daily": [{**row, "day": row["day"].isoformat()} for row in daily],
        }


@router.get("/admin/capacity")
async def get_capacity(context: Context, _: CurrentAdmin) -> dict[str, Any]:
    """Return agents by state, cold starts, measured usage and the capacity estimate."""
    async with context.db.session() as session:
        state_rows = await session.execute(select(Agent.state, func.count()).group_by(Agent.state))
        states = {state: count for state, count in state_rows.all()}
        wakes = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "agent.woke")
                .order_by(AuditEvent.id.desc())
                .limit(50)
            )
        ).all()
        reaps = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "agent.slept")
        )
    seconds = sorted(float(w.detail.get("seconds", 0.0)) for w in wakes)
    measured = await capacity.measure(context.prometheus, context.settings.agent_memory_bytes)
    return {
        "agents": {"total": sum(states.values()), **states},
        "idle_seconds": await context.reaper.idle_seconds(),
        "cold_starts": {
            "count": len(seconds),
            "median_seconds": seconds[len(seconds) // 2] if seconds else None,
            "max_seconds": seconds[-1] if seconds else None,
            "recent": [
                {
                    "agent_id": w.agent_id,
                    "at": w.at.isoformat(),
                    "seconds": w.detail.get("seconds"),
                    "trigger": w.detail.get("trigger"),
                }
                for w in wakes[:10]
            ],
        },
        "sleeps": int(reaps or 0),
        **measured,
    }


@router.get("/admin/settings", response_model=SettingsBody)
async def get_settings_(context: Context, _: CurrentAdmin) -> SettingsBody:
    """Return the runtime settings."""
    return SettingsBody(idle_seconds=await context.reaper.idle_seconds())


@router.put("/admin/settings", response_model=SettingsBody)
async def put_settings(body: SettingsBody, context: Context, admin: CurrentAdmin) -> SettingsBody:
    """Change how long an agent may be idle before it is put to sleep."""
    async with context.db.session() as session:
        row = await session.get(Setting, "fleet")
        if row is None:
            session.add(Setting(key="fleet", value=body.model_dump()))
        else:
            row.value = body.model_dump()
        await audit.record(
            session,
            kind="policy",
            action="settings.changed",
            person_id=admin.id,
            summary=f"{admin.name} set the idle limit to {body.idle_seconds}s",
        )
    return body


@router.get("/admin/skills", response_model=list[SkillOut])
async def all_skills(context: Context, _: CurrentAdmin) -> list[Any]:
    """Return the skill library: role bundle skills, shared skills and every proposal."""
    return [*await context.skills.all(), *context.skills.bundle_skills()]


@router.post("/admin/skills/{skill_id}/approve", response_model=SkillOut)
async def approve_shared_skill(
    skill_id: str, body: SkillDecision, context: Context, admin: CurrentAdmin
) -> SkillProposal:
    """Approve a skill proposed for the shared library and choose who receives it."""
    return await context.skills.decide(skill_id, admin, approve=True, roles=body.roles)


@router.post("/admin/skills/{skill_id}/reject", response_model=SkillOut)
async def reject_shared_skill(
    skill_id: str, context: Context, admin: CurrentAdmin
) -> SkillProposal:
    """Reject a skill proposed for the shared library."""
    return await context.skills.decide(skill_id, admin, approve=False)


@router.get("/me/skills", response_model=list[SkillOut])
async def my_skills(context: Context, person: CurrentPerson) -> list[Any]:
    """Return my role's skills, the shared skills I receive and my agent's own proposals."""
    mine = await context.skills.visible_to(person)
    bundle = [s for s in context.skills.bundle_skills() if person.role in s["roles"]]
    return [*mine, *bundle]


@router.post("/me/skills/{skill_id}/approve", response_model=SkillOut)
async def approve_my_skill(skill_id: str, context: Context, person: CurrentPerson) -> SkillProposal:
    """Approve a personal skill my agent proposed. It is mounted the next time the agent starts."""
    return await context.skills.decide(skill_id, person, approve=True)


@router.post("/me/skills/{skill_id}/reject", response_model=SkillOut)
async def reject_my_skill(skill_id: str, context: Context, person: CurrentPerson) -> SkillProposal:
    """Reject a personal skill my agent proposed."""
    return await context.skills.decide(skill_id, person, approve=False)


async def _systems(context: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {context.settings.internal_token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{context.settings.systems_url}{path}", json=body, headers=headers
            )
    except httpx.HTTPError as exc:
        raise UpstreamError(f"corporate systems: {exc}") from exc
    if response.status_code >= 400:
        raise UpstreamError(f"corporate systems: {response.text[:200]}")
    return dict(response.json())


@router.post("/admin/demo/appeal-decision")
async def demo_appeal_decision(
    body: AppealDecisionIn, context: Context, admin: CurrentAdmin
) -> dict[str, Any]:
    """Demo control: record the payer's decision on an open appeal."""
    result = await _systems(context, "/billing/simulate/appeal-decision", body.model_dump())
    async with context.db.session() as session:
        await audit.record(
            session,
            kind="policy",
            action="demo.appeal_decision",
            person_id=admin.id,
            system="billing",
            summary=f"Simulated payer decision on {body.claim_id}: {body.outcome}",
        )
    return result


@router.post("/admin/demo/reset")
async def demo_reset(context: Context, admin: CurrentAdmin) -> dict[str, str]:
    """Demo control: restore the corporate systems and the collaboration state to the seed.

    Agents, their volumes, usage history and the audit trail are kept.
    """
    await _systems(context, "/admin/reset", {})
    async with context.db.session() as session:
        await session.execute(update(Agent).values(current_run_id=None))
        for table in (Approval, AgentRequest, AgentSession, KbDocument, Case):
            await session.execute(delete(table))
        await audit.record(
            session,
            kind="policy",
            action="demo.reset",
            person_id=admin.id,
            summary=f"{admin.name} reset the demo data",
        )
    await seed_cases(context, _load(context.settings.seed_dir, "cases.yaml"))
    await seed_knowledge(context, _load(context.settings.seed_dir, "knowledge.json"))
    return {"status": "reset"}
