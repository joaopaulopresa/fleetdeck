"""Request and response bodies of the public API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PersonOut(BaseModel):
    """A person as shown in the UI."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    title: str
    role: str
    department: str
    is_admin: bool


class LoginIn(BaseModel):
    """Demo sign-on: pick one of the built-in people."""

    person_id: str


class LoginOut(BaseModel):
    """A session token and the person it belongs to."""

    token: str
    person: PersonOut


class AgentOut(BaseModel):
    """An agent as shown in the fleet view."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str | None
    display_name: str
    bundle: str
    tier: str
    state: str
    suspended: bool
    created_at: datetime
    last_activity_at: datetime | None
    last_wake_seconds: float | None
    wake_count: int


class AgentCreate(BaseModel):
    """Provision an agent for a person, or a service agent when ``person_id`` is omitted."""

    agent_id: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")
    bundle: str
    person_id: str | None = None
    display_name: str | None = None
    tier: str = "strict"


class WakeOut(BaseModel):
    """Result of a wake request."""

    agent_id: str
    waited_seconds: float
