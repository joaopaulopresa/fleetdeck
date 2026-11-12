"""Tables of the control plane."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from fleetdeck_control.db import Base


class Person(Base):
    """An employee of the company. Each person can have one personal agent."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    department: Mapped[str] = mapped_column(String(60))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Agent(Base):
    """A Hermes agent managed by the platform, personal or (with no person) a service agent."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id"), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    bundle: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(20), default="strict")
    #: asleep | waking | active | provisioning | suspended | error
    state: Mapped[str] = mapped_column(String(20), default="provisioning")
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    model_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    connector_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_wake_seconds: Mapped[float | None] = mapped_column()
    wake_count: Mapped[int] = mapped_column(default=0)


class AuditEvent(Base):
    """One append-only audit record. The database refuses updates and deletes on this table."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    #: model_call | tool_call | request | approval | agent | sign_in | policy | egress | schedule
    kind: Mapped[str] = mapped_column(String(30), index=True)
    action: Mapped[str] = mapped_column(String(80))
    #: allowed | denied | info
    outcome: Mapped[str] = mapped_column(String(20), default="info")
    person_id: Mapped[str | None] = mapped_column(String(40), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), index=True)
    system: Mapped[str | None] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(60))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
