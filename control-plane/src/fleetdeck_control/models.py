"""Tables of the control plane."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    #: The run the agent is executing now; the model gateway routes and attributes by it.
    current_run_id: Mapped[str | None] = mapped_column(String(40))


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


#: Data classes in increasing order of sensitivity.
DATA_CLASSES = ("general", "confidential", "restricted")


def most_sensitive(*classes: str) -> str:
    """Return the most sensitive of the given data classes."""
    return max(classes, key=DATA_CLASSES.index)


class AgentSession(Base):
    """One Hermes conversation of an agent: a chat, the handling of a request, a scheduled job.

    A session has a data class. It only ever rises: once restricted content has entered the
    conversation, every later model call of that session uses the restricted route.
    """

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    #: chat | request | schedule
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200), default="")
    case_id: Mapped[str | None] = mapped_column(String(40))
    data_class: Mapped[str] = mapped_column(String(20), default="general")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    """One execution of an agent: input in, events, output out."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    #: chat | request | schedule
    trigger: Mapped[str] = mapped_column(String(20))
    #: queued | waking | running | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="queued")
    input: Mapped[str] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    hermes_run_id: Mapped[str | None] = mapped_column(String(60))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    wake_seconds: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """One entry of a session transcript, as shown on the employee page."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(40))
    #: user | assistant | tool | system
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCall(Base):
    """One call from an agent to a model, as recorded by the model gateway."""

    __tablename__ = "model_calls"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(40), index=True)
    person_id: Mapped[str | None] = mapped_column(String(40), index=True)
    department: Mapped[str | None] = mapped_column(String(60))
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    session_id: Mapped[str | None] = mapped_column(String(40))
    case_id: Mapped[str | None] = mapped_column(String(40))
    trigger: Mapped[str | None] = mapped_column(String(20))
    data_class: Mapped[str] = mapped_column(String(20))
    #: default | restricted
    route: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=200)
    trace_id: Mapped[str | None] = mapped_column(String(64))


class Case(Base):
    """A case space: a patient episode, a claim dispute, a legal matter, a tax portfolio.

    Documents, requests and history belong to the case, not to a person. Row-level security
    hides a case, its members and its documents from everyone who is not a member.
    """

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: patient_episode | claim_dispute | legal_matter | tax_portfolio
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    data_class: Mapped[str] = mapped_column(String(20), default="confidential")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseMember(Base):
    """A person's membership of a case."""

    __tablename__ = "case_members"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), primary_key=True
    )
    #: owner | member
    role_in_case: Mapped[str] = mapped_column(String(20), default="member")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KbDocument(Base):
    """A knowledge-base document: shared reference material, or a document of one case."""

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    #: template | protocol | rule | dossier | notice | note
    kind: Mapped[str] = mapped_column(String(30))
    data_class: Mapped[str] = mapped_column(String(20), default="general")
    #: For documents without a case: the roles that may read it. Empty means everyone.
    audience_roles: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    body: Mapped[str] = mapped_column(Text)
    #: published | draft
    status: Mapped[str] = mapped_column(String(20), default="published")
    author_person_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class System(Base):
    """A system agents reach through a connector, with the class of the data it holds."""

    __tablename__ = "systems"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    data_class: Mapped[str] = mapped_column(String(20), default="confidential")


class ToolPermission(Base):
    """Which role may use which tool of which system, for all records or only within a case."""

    __tablename__ = "tool_permissions"

    system: Mapped[str] = mapped_column(
        ForeignKey("systems.name", ondelete="CASCADE"), primary_key=True
    )
    tool: Mapped[str] = mapped_column(String(60), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: all: any record. case: only records of a case the person is a member of.
    scope: Mapped[str] = mapped_column(String(10), default="all")


class Approval(Base):
    """Something an agent prepared that its person must confirm before it takes effect."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    #: draft (a write prepared in a system) | reply (an answer to another agent's request)
    kind: Mapped[str] = mapped_column(String(20))
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(40))
    case_id: Mapped[str | None] = mapped_column(String(40))
    system: Mapped[str] = mapped_column(String(40))
    #: Id of the draft in its system, or of the request being answered.
    ref: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    #: pending | approved | declined
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
