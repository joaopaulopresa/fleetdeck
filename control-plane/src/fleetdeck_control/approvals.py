"""Approvals: what an agent prepared stays a draft until its person confirms it."""

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from fleetdeck_common.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UpstreamError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleetdeck_control import audit, metrics
from fleetdeck_control.db import Database
from fleetdeck_control.models import Approval, KbDocument, Person
from fleetdeck_control.runs import new_id

log = logging.getLogger(__name__)


class DraftConfirmer(Protocol):
    """Makes a draft real in the system that holds it, or discards it."""

    async def confirm(self, system: str, ref: str, *, person_id: str, body: str) -> None:
        """Turn the draft into the real record (signed, filed, sent, posted)."""

    async def discard(self, system: str, ref: str, *, person_id: str) -> None:
        """Throw the draft away."""


class HttpDraftConfirmer:
    """Confirms drafts in the corporate systems over their own APIs."""

    def __init__(self, base_url: str, internal_token: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=15.0, headers={"Authorization": f"Bearer {internal_token}"}
        )

    async def _post(self, system: str, ref: str, action: str, payload: dict[str, Any]) -> None:
        url = f"{self._base}/{system}/drafts/{ref}/{action}"
        try:
            response = await self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{system}: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamError(f"{system}: {response.status_code} {response.text[:200]}")

    async def confirm(self, system: str, ref: str, *, person_id: str, body: str) -> None:
        """Confirm the draft in its system."""
        await self._post(system, ref, "confirm", {"person_id": person_id, "body": body})

    async def discard(self, system: str, ref: str, *, person_id: str) -> None:
        """Discard the draft in its system."""
        await self._post(system, ref, "discard", {"person_id": person_id})


class ReplyReleaser(Protocol):
    """Releases or withholds an agent's reply to another agent's request (the broker)."""

    async def release_reply(self, request_id: str, *, body: str, approved_by: str) -> None:
        """Send the approved reply to the asking agent."""

    async def withhold_reply(self, request_id: str, *, declined_by: str) -> None:
        """Tell the asking agent that no reply will come."""


class ApprovalService:
    """Creates and decides approvals."""

    def __init__(self, db: Database, confirmer: DraftConfirmer) -> None:
        self._db = db
        self._confirmer = confirmer
        self.replies: ReplyReleaser | None = None

    async def create(
        self,
        session: AsyncSession,
        *,
        kind: str,
        person_id: str,
        agent_id: str | None,
        case_id: str | None,
        system: str,
        ref: str,
        title: str,
        body: str,
        detail: dict[str, Any] | None = None,
    ) -> Approval:
        """Add a pending approval in the caller's transaction."""
        approval = Approval(
            id=new_id("apr"),
            kind=kind,
            person_id=person_id,
            agent_id=agent_id,
            case_id=case_id,
            system=system,
            ref=ref,
            title=title[:200],
            body=body,
            detail=detail or {},
        )
        session.add(approval)
        await audit.record(
            session,
            kind="approval",
            action=f"{kind}.prepared",
            person_id=person_id,
            agent_id=agent_id,
            case_id=case_id,
            system=system,
            summary=f"Waiting for confirmation: {title}",
            detail={"ref": ref},
        )
        return approval

    async def pending_for(self, person_id: str, status: str = "pending") -> list[Approval]:
        """Return a person's approvals with the given status, newest first."""
        async with self._db.session() as session:
            rows = await session.scalars(
                select(Approval)
                .where(Approval.person_id == person_id, Approval.status == status)
                .order_by(Approval.created_at.desc())
            )
            return list(rows.all())

    async def decide(
        self, approval_id: str, person: Person, *, approve: bool, body: str | None = None
    ) -> Approval:
        """Approve or decline. Only the person the approval belongs to may decide it."""
        async with self._db.session() as session:
            approval = await session.get(Approval, approval_id, with_for_update=True)
            if approval is None:
                raise NotFoundError("approval not found")
            if approval.person_id != person.id:
                raise PermissionDeniedError("only the owner can decide this")
            if approval.status != "pending":
                raise ConflictError(f"already {approval.status}")
            final_body = body if body is not None else approval.body
            approval.body = final_body
            approval.status = "approved" if approve else "declined"
            approval.decided_at = datetime.now(UTC)
            waited = (approval.decided_at - approval.created_at).total_seconds()
            metrics.APPROVAL_SECONDS.observe(max(waited, 0.0))
            # Recorded before the effect, so the trail reads: decided, then what it caused.
            await audit.record(
                session,
                kind="approval",
                action=f"{approval.kind}.{'approved' if approve else 'declined'}",
                outcome="allowed" if approve else "denied",
                person_id=person.id,
                agent_id=approval.agent_id,
                case_id=approval.case_id,
                system=approval.system,
                summary=f"{person.name} {'confirmed' if approve else 'declined'}: {approval.title}",
                detail={"ref": approval.ref, "edited": body is not None},
            )
            if approve:
                await self._apply(session, approval, person.id, final_body)
            else:
                await self._drop(session, approval, person.id)
        return approval

    async def _apply(
        self, session: AsyncSession, approval: Approval, person_id: str, body: str
    ) -> None:
        if approval.kind == "reply":
            if self.replies is None:
                raise UpstreamError("no broker configured")
            await self.replies.release_reply(approval.ref, body=body, approved_by=person_id)
        elif approval.system == "knowledge":
            document = await session.get(KbDocument, approval.ref)
            if document is None:
                raise NotFoundError("document not found")
            document.body = body
            document.status = "published"
        else:
            await self._confirmer.confirm(
                approval.system, approval.ref, person_id=person_id, body=body
            )

    async def _drop(self, session: AsyncSession, approval: Approval, person_id: str) -> None:
        if approval.kind == "reply":
            if self.replies is not None:
                await self.replies.withhold_reply(approval.ref, declined_by=person_id)
        elif approval.system == "knowledge":
            document = await session.get(KbDocument, approval.ref)
            if document is not None:
                await session.delete(document)
        else:
            await self._confirmer.discard(approval.system, approval.ref, person_id=person_id)
