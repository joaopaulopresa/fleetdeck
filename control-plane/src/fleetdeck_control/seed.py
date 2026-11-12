"""Loads the synthetic demo company and provisions its agents. Safe to run repeatedly."""

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from fleetdeck_common.errors import ConflictError

from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.context import AppContext, build_context
from fleetdeck_control.models import Case, CaseMember, KbDocument, Person, System, ToolPermission
from fleetdeck_control.settings import get_settings

log = logging.getLogger(__name__)

_PERSON_FIELDS = ("id", "name", "email", "title", "role", "department", "is_admin")


async def seed_people(context: AppContext, people: list[dict[str, Any]]) -> None:
    """Insert or update the people, then provision an agent for each one that has a bundle."""
    async with context.db.session() as session:
        for row in people:
            values = {key: row[key] for key in _PERSON_FIELDS}
            person = await session.get(Person, row["id"])
            if person is None:
                session.add(Person(**values))
            else:
                for key, value in values.items():
                    setattr(person, key, value)
    for row in people:
        if not row.get("bundle"):
            continue
        try:
            await context.agents.create(
                NewAgent(agent_id=row["id"], person_id=row["id"], bundle=row["bundle"])
            )
        except ConflictError:
            log.info("agent %s already exists", row["id"])


async def seed_access(context: AppContext, systems: list[dict[str, Any]]) -> None:
    """Insert the connected systems and, for systems seen for the first time, their permissions.

    Permissions an administrator changed later are left alone.
    """
    async with context.db.session() as session:
        for row in systems:
            system = await session.get(System, row["name"])
            fields = {key: row[key] for key in ("title", "description", "data_class")}
            if system is not None:
                for key, value in fields.items():
                    setattr(system, key, value)
                continue
            session.add(System(name=row["name"], **fields))
            await session.flush()
            for tool, grants in row.get("tools", {}).items():
                for role, scope in grants.items():
                    session.add(
                        ToolPermission(system=row["name"], tool=tool, role=role, scope=scope)
                    )


async def seed_cases(context: AppContext, cases: list[dict[str, Any]]) -> None:
    """Insert the case spaces with their members and documents if they are missing."""
    async with context.db.session() as session:
        for row in cases:
            if await session.get(Case, row["id"]) is not None:
                continue
            session.add(
                Case(**{key: row[key] for key in ("id", "kind", "title", "summary", "data_class")})
            )
            await session.flush()
            for person_id, role_in_case in row.get("members", {}).items():
                session.add(
                    CaseMember(case_id=row["id"], person_id=person_id, role_in_case=role_in_case)
                )


async def seed_knowledge(context: AppContext, documents: list[dict[str, Any]]) -> None:
    """Insert the shared knowledge-base documents if they are missing."""
    async with context.db.session() as session:
        for row in documents:
            if await session.get(KbDocument, row["id"]) is None:
                session.add(KbDocument(status="published", **row))


def _load(path: Path, name: str) -> list[dict[str, Any]]:
    file = path / name
    if not file.is_file():
        return []
    text = file.read_text()
    loaded = yaml.safe_load(text) if file.suffix in (".yaml", ".yml") else json.loads(text)
    return list(loaded or [])


async def seed_all(path: Path) -> None:
    """Load every seed file found in ``path``."""
    context = build_context(get_settings())
    try:
        people = _load(path, "people.json")
        await seed_people(context, people)
        await seed_access(context, _load(path, "systems.yaml"))
        await seed_cases(context, _load(path, "cases.yaml"))
        await seed_knowledge(context, _load(path, "knowledge.json"))
        log.info("seeded %d people", len(people))
    finally:
        await context.db.dispose()
