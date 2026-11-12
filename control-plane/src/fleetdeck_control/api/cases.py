"""Case spaces and approvals for employees; systems, permissions and cases for administrators."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fleetdeck_common.errors import NotFoundError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select

from fleetdeck_control import audit
from fleetdeck_control.auth import Context, CurrentAdmin, CurrentPerson
from fleetdeck_control.models import (
    Approval,
    Case,
    CaseMember,
    KbDocument,
    Person,
    System,
    ToolPermission,
)

router = APIRouter(prefix="/api", tags=["cases"])


class CaseOut(BaseModel):
    """A case as its members see it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    title: str
    summary: str
    data_class: str
    status: str


class MemberOut(BaseModel):
    """A member of a case."""

    person_id: str
    name: str
    title: str
    role_in_case: str


class DocumentOut(BaseModel):
    """A document of a case or of the shared knowledge base."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str | None
    title: str
    kind: str
    data_class: str
    status: str
    created_at: datetime


class DocumentBody(DocumentOut):
    """A document with its text."""

    body: str


class CaseDetail(CaseOut):
    """A case with its members and documents."""

    members: list[MemberOut]
    documents: list[DocumentOut]


class ApprovalOut(BaseModel):
    """Something waiting for, or decided by, its person."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    case_id: str | None
    system: str
    ref: str
    title: str
    body: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    detail: dict[str, Any]


class DecisionIn(BaseModel):
    """Optionally an edited text to use instead of the agent's draft."""

    body: str | None = None


class PermissionRow(BaseModel):
    """One role's access to one tool."""

    model_config = ConfigDict(from_attributes=True)

    tool: str
    role: str
    scope: str = "all"


class SystemOut(BaseModel):
    """A connected system with its permission matrix."""

    name: str
    title: str
    description: str
    data_class: str
    permissions: list[PermissionRow]


class AdminCaseOut(CaseOut):
    """Case metadata for administrators: who is in it, never what is in it."""

    members: list[str]
    documents: int


@router.get("/me/cases", response_model=list[CaseOut])
async def my_cases(context: Context, person: CurrentPerson) -> list[Case]:
    """List the cases I am a member of. Row-level security hides every other case."""
    async with context.db.session(person_id=person.id) as session:
        return list((await session.scalars(select(Case).order_by(Case.created_at.desc()))).all())


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def case_detail(case_id: str, context: Context, person: CurrentPerson) -> CaseDetail:
    """Return a case with members and documents; 404 for non-members, as if it did not exist."""
    async with context.db.session(person_id=person.id) as session:
        case = await session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")
        members = (
            await session.execute(
                select(CaseMember, Person)
                .join(Person, Person.id == CaseMember.person_id)
                .where(CaseMember.case_id == case_id)
                .order_by(Person.name)
            )
        ).all()
        documents = (
            await session.scalars(
                select(KbDocument)
                .where(KbDocument.case_id == case_id)
                .order_by(KbDocument.created_at.desc())
            )
        ).all()
    return CaseDetail(
        **CaseOut.model_validate(case).model_dump(),
        members=[
            MemberOut(person_id=p.id, name=p.name, title=p.title, role_in_case=m.role_in_case)
            for m, p in members
        ],
        documents=[DocumentOut.model_validate(d) for d in documents],
    )


@router.get("/documents/{document_id}", response_model=DocumentBody)
async def document(document_id: str, context: Context, person: CurrentPerson) -> KbDocument:
    """Return one document I am allowed to read."""
    async with context.db.session(person_id=person.id) as session:
        row = await session.get(KbDocument, document_id)
    if row is None:
        raise NotFoundError("document not found")
    return row


@router.get("/me/approvals", response_model=list[ApprovalOut])
async def my_approvals(
    context: Context, person: CurrentPerson, status: str = "pending"
) -> list[Approval]:
    """List what waits for my confirmation (or what I decided, with ``status``)."""
    return await context.approvals.pending_for(person.id, status)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str, body: DecisionIn, context: Context, person: CurrentPerson
) -> Approval:
    """Confirm a draft or release a reply, optionally with my edits."""
    return await context.approvals.decide(approval_id, person, approve=True, body=body.body)


@router.post("/approvals/{approval_id}/decline", response_model=ApprovalOut)
async def decline(approval_id: str, context: Context, person: CurrentPerson) -> Approval:
    """Discard a draft or withhold a reply."""
    return await context.approvals.decide(approval_id, person, approve=False)


@router.get("/admin/systems", response_model=list[SystemOut])
async def systems(context: Context, _: CurrentAdmin) -> list[SystemOut]:
    """List connected systems with which role may use which tool."""
    async with context.db.session() as session:
        rows = (await session.scalars(select(System).order_by(System.name))).all()
        permissions = (
            await session.scalars(
                select(ToolPermission).order_by(ToolPermission.tool, ToolPermission.role)
            )
        ).all()
    return [
        SystemOut(
            name=s.name,
            title=s.title,
            description=s.description,
            data_class=s.data_class,
            permissions=[
                PermissionRow.model_validate(p) for p in permissions if p.system == s.name
            ],
        )
        for s in rows
    ]


@router.put("/admin/systems/{name}/permissions", response_model=SystemOut)
async def set_permissions(
    name: str, rows: list[PermissionRow], context: Context, admin: CurrentAdmin
) -> SystemOut:
    """Replace a system's permission matrix. Takes effect on the next connector call."""
    async with context.db.session() as session:
        system = await session.get(System, name)
        if system is None:
            raise NotFoundError("system not found")
        await session.execute(delete(ToolPermission).where(ToolPermission.system == name))
        session.add_all(
            ToolPermission(system=name, tool=r.tool, role=r.role, scope=r.scope) for r in rows
        )
        await audit.record(
            session,
            kind="policy",
            action="permissions.changed",
            person_id=admin.id,
            system=name,
            summary=f"{admin.name} changed who may use {name}",
            detail={"rows": [r.model_dump() for r in rows]},
        )
    return SystemOut(
        name=system.name,
        title=system.title,
        description=system.description,
        data_class=system.data_class,
        permissions=rows,
    )


@router.get("/admin/cases", response_model=list[AdminCaseOut])
async def admin_cases(context: Context, _: CurrentAdmin) -> list[AdminCaseOut]:
    """List case metadata and membership. Documents and their content are not exposed here."""
    async with context.db.session() as session:
        cases = (await session.scalars(select(Case).order_by(Case.id))).all()
        members = (await session.scalars(select(CaseMember))).all()
        rows = await session.execute(
            select(KbDocument.case_id, func.count()).group_by(KbDocument.case_id)
        )
        counts = {case_id: count for case_id, count in rows.all()}
    return [
        AdminCaseOut(
            **CaseOut.model_validate(c).model_dump(),
            members=sorted(m.person_id for m in members if m.case_id == c.id),
            documents=int(counts.get(c.id, 0)),
        )
        for c in cases
    ]
