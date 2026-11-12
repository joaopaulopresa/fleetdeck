"""Loads the synthetic demo company and provisions its agents. Safe to run repeatedly."""

import json
import logging
from pathlib import Path
from typing import Any

from fleetdeck_common.errors import ConflictError

from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.context import AppContext, build_context
from fleetdeck_control.models import Person
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


async def seed_all(path: Path) -> None:
    """Load every seed file found in ``path``."""
    context = build_context(get_settings())
    try:
        people = json.loads((path / "people.json").read_text())
        await seed_people(context, people)
        log.info("seeded %d people", len(people))
    finally:
        await context.db.dispose()
