"""Role bundles: what an agent of a given role starts with.

A bundle is one YAML file, ``bundles/<name>/bundle.yaml``: the brief, the connectors the role
may reach, the Hermes toolsets to switch off, the skills and the schedules. Nothing comes from a
public skills hub.
"""

from pathlib import Path

import yaml
from fleetdeck_common.errors import NotFoundError
from pydantic import BaseModel, Field

#: Toolsets no role bundle gets unless it asks: nothing to act with if a prompt injection lands,
#: and fewer tokens per turn (measured in the spike: 23,600 to 10,500 input tokens).
DEFAULT_DISABLED_TOOLSETS = (
    "web",
    "browser",
    "terminal",
    "file",
    "code_execution",
    "vision",
    "image_gen",
    "delegation",
    "cronjob",
    "connections",
    "tts",
)

#: Hermes loads its identity file whole only below this size (its dynamic cap has a 20K floor).
BRIEF_MAX_CHARS = 12_000


class Skill(BaseModel):
    """A procedure the agent can load on demand."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,48}$")
    description: str
    body: str


class Schedule(BaseModel):
    """A recurring job run by the central scheduler."""

    name: str
    cron: str
    prompt: str


class Bundle(BaseModel):
    """Everything a role's agent starts with."""

    name: str
    description: str = ""
    brief: str = Field(max_length=BRIEF_MAX_CHARS)
    connectors: list[str] = Field(default_factory=list)
    disabled_toolsets: list[str] = Field(default_factory=lambda: list(DEFAULT_DISABLED_TOOLSETS))
    skills: list[Skill] = Field(default_factory=list)
    schedules: list[Schedule] = Field(default_factory=list)


def load_bundle(bundles_dir: Path, name: str) -> Bundle:
    """Load and validate ``<bundles_dir>/<name>/bundle.yaml``."""
    path = bundles_dir / name / "bundle.yaml"
    if not path.is_file():
        raise NotFoundError(f"bundle {name!r} not found in {bundles_dir}")
    return Bundle.model_validate(yaml.safe_load(path.read_text()))


def list_bundles(bundles_dir: Path) -> list[Bundle]:
    """Load every bundle in the directory, sorted by name."""
    names = sorted(p.parent.name for p in bundles_dir.glob("*/bundle.yaml"))
    return [load_bundle(bundles_dir, name) for name in names]
