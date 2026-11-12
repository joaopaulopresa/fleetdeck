"""Skills: role bundle skills, the reviewed shared library and each person's own skills."""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleetdeck_common.bundles import list_bundles
from fleetdeck_common.errors import ConflictError, NotFoundError, PermissionDeniedError
from sqlalchemy import or_, select

from fleetdeck_control import audit
from fleetdeck_control.agents.service import spec_for
from fleetdeck_control.cluster import AgentCluster
from fleetdeck_control.db import Database
from fleetdeck_control.models import Agent, Person, SkillProposal
from fleetdeck_control.runs import new_id

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


class SkillService:
    """Proposals, reviews and delivery of approved skills to agents."""

    def __init__(self, db: Database, cluster: AgentCluster, bundles_dir: Path) -> None:
        self._db = db
        self._cluster = cluster
        self._bundles_dir = bundles_dir

    def bundle_skills(self) -> list[dict[str, Any]]:
        """Return the skills shipped in role bundles."""
        return [
            {
                "id": f"bundle:{bundle.name}:{skill.name}",
                "name": skill.name,
                "description": skill.description,
                "body": skill.body,
                "scope": "bundle",
                "roles": [bundle.name],
                "status": "approved",
            }
            for bundle in list_bundles(self._bundles_dir)
            for skill in bundle.skills
        ]

    async def propose(
        self, agent: Agent, *, name: str, description: str, body: str, share: bool
    ) -> SkillProposal:
        """Record a skill an agent wants to keep. It does nothing until someone approves it."""
        if not _NAME.fullmatch(name):
            raise ValueError("skill names are lower-case words joined by hyphens")
        if agent.person_id is None:
            raise PermissionDeniedError("service agents cannot propose skills")
        proposal = SkillProposal(
            id=new_id("skl"),
            name=name,
            description=description[:300],
            body=body,
            scope="shared" if share else "personal",
            owner_person_id=agent.person_id,
            proposed_by_agent=agent.id,
        )
        async with self._db.session() as session:
            session.add(proposal)
            await audit.record(
                session,
                kind="policy",
                action="skill.proposed",
                agent_id=agent.id,
                person_id=agent.person_id,
                system="fleet",
                summary=f"Skill {name} proposed ({proposal.scope})",
            )
        return proposal

    async def visible_to(self, person: Person) -> list[SkillProposal]:
        """Return a person's own skills and proposals, and the shared skills of their role."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SkillProposal)
                .where(
                    or_(
                        SkillProposal.owner_person_id == person.id,
                        (SkillProposal.scope == "shared")
                        & (SkillProposal.status == "approved")
                        & SkillProposal.roles.contains([person.role]),
                    )
                )
                .order_by(SkillProposal.created_at.desc())
            )
            return list(rows.all())

    async def all(self) -> list[SkillProposal]:
        """Return every proposal and decided skill, newest first."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(SkillProposal).order_by(SkillProposal.created_at.desc())
            )
            return list(rows.all())

    async def decide(
        self, skill_id: str, decider: Person, *, approve: bool, roles: list[str] | None = None
    ) -> SkillProposal:
        """Approve or reject a proposal.

        A personal skill is decided by its owner. A shared skill is decided by an administrator,
        who also chooses the roles that receive it.
        """
        async with self._db.session() as session:
            skill = await session.get(SkillProposal, skill_id, with_for_update=True)
            if skill is None:
                raise NotFoundError("skill not found")
            if skill.scope == "shared" and not decider.is_admin:
                raise PermissionDeniedError("shared skills are reviewed by an administrator")
            if skill.scope == "personal" and skill.owner_person_id != decider.id:
                raise PermissionDeniedError("only the owner decides a personal skill")
            if skill.status != "proposed":
                raise ConflictError(f"already {skill.status}")
            skill.status = "approved" if approve else "rejected"
            skill.decided_by = decider.id
            skill.decided_at = datetime.now(UTC)
            if skill.scope == "shared" and approve:
                owner = await session.get(Person, skill.owner_person_id)
                skill.roles = roles or ([owner.role] if owner else [])
            await audit.record(
                session,
                kind="policy",
                action=f"skill.{skill.status}",
                person_id=decider.id,
                agent_id=skill.proposed_by_agent,
                system="fleet",
                outcome="allowed" if approve else "denied",
                summary=f"{decider.name} {skill.status} the {skill.scope} skill {skill.name}",
            )
        if approve:
            await self.sync_agents()
        return skill

    async def skills_for(self, agent: Agent, person: Person | None) -> list[dict[str, str]]:
        """Return the approved skills an agent receives on top of its role bundle."""
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(SkillProposal)
                    .where(SkillProposal.status == "approved")
                    .order_by(SkillProposal.name)
                )
            ).all()
        role = person.role if person else "service"
        return [
            {"name": s.name, "description": s.description, "body": s.body}
            for s in rows
            if (s.scope == "personal" and s.owner_person_id == agent.person_id)
            or (s.scope == "shared" and role in s.roles)
        ]

    async def sync_agents(self) -> None:
        """Write each agent's approved skills into its custom resource; the operator mounts them."""
        async with self._db.session() as session:
            agents = (await session.scalars(select(Agent))).all()
            people = {p.id: p for p in (await session.scalars(select(Person))).all()}
        for agent in agents:
            person = people.get(agent.person_id) if agent.person_id else None
            status = await self._cluster.status(agent.id)
            awake = bool(status and status.phase in ("Active", "Waking"))
            await self._cluster.apply(
                spec_for(
                    agent, person, awake=awake, extra_skills=await self.skills_for(agent, person)
                )
            )
