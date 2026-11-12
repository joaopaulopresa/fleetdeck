"""Connector for the knowledge base and the documents of each case.

Unlike the other connectors this one reads PostgreSQL directly, as a login that is subject to
row-level security. Which documents exist for the caller is decided by the database.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from fleetdeck_connectors.base import AccessDeniedError, Deps, Result, Tool, safe

INSTRUCTIONS = (
    "The knowledge base: shared templates, protocols and rules, and the documents of the cases "
    "you belong to. Documents you write into a case are drafts until your person approves them."
)
_ORDER = ("general", "confidential", "restricted")
_SEARCH = """
    select id, case_id, title, kind, data_class, status,
           ts_headline('english', body, websearch_to_tsquery('english', $1),
                       'MaxWords=40, MinWords=15') as excerpt
    from kb_documents
    where (search @@ websearch_to_tsquery('english', $1) or title ilike '%' || $1 || '%')
      and ($2::text is null or case_id = $2)
    order by ts_rank(search, websearch_to_tsquery('english', $1)) desc
    limit 8
"""


class ScopedDatabase:
    """Runs queries as one person. PostgreSQL row-level security does the filtering."""

    def __init__(self, url: str, *, scoped_role: str | None = None) -> None:
        self._url = url.replace("postgresql+asyncpg://", "postgresql://")
        #: Set when the login is not itself the scoped role (tests use the owner login).
        self._scoped_role = scoped_role
        self._pool: asyncpg.Pool | None = None

    @asynccontextmanager
    async def as_person(self, person_id: str) -> AsyncIterator[asyncpg.Connection]:
        """Open a transaction in which ``app.person_id`` is the given person."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute("select set_config('app.person_id', $1, true)", person_id)
            if self._scoped_role:
                await connection.execute(f"set local role {self._scoped_role}")
            yield connection

    async def close(self) -> None:
        """Close the pool."""
        if self._pool is not None:
            await self._pool.close()


def tools(deps: Deps, database: ScopedDatabase) -> list[Tool]:
    """Return the knowledge tools bound to ``deps`` and the scoped database."""

    def person_of(decision: Any) -> str:
        person_id = decision.identity.person_id
        if person_id is None:
            raise AccessDeniedError("service agents have no knowledge-base access")
        return str(person_id)

    @safe
    async def search_knowledge(query: str, case_id: str = "") -> Result:
        """Search the documents I may read: shared reference material and my cases' documents.

        Args:
            query: Words to search for.
            case_id: Optional: only documents of this case.

        """
        decision = await deps.allow("search_knowledge", record=query[:60])
        async with database.as_person(person_of(decision)) as connection:
            rows = await connection.fetch(_SEARCH, query, case_id or None)
        return {"documents": [dict(row) for row in rows]}

    @safe
    async def get_document(document_id: str) -> Result:
        """Return a document with its full text.

        Args:
            document_id: The document id from a search result or a case.

        """
        decision = await deps.allow("get_document", record=document_id)
        async with database.as_person(person_of(decision)) as connection:
            row = await connection.fetchrow(
                "select id, case_id, title, kind, data_class, status, body "
                "from kb_documents where id = $1",
                document_id,
            )
        if row is None:
            # Not there, or not for this person: the answer is the same.
            return {"error": "not found", "reason": f"no document {document_id}"}
        if row["data_class"] != "general":
            await deps.control.raise_data_class(
                decision.identity.agent_id, row["data_class"], deps.system
            )
        return dict(row)

    @safe
    async def draft_case_document(case_id: str, title: str, kind: str, body: str) -> Result:
        """Write a document into one of my cases as a draft for my person to approve.

        Args:
            case_id: The case the document belongs to.
            title: The document title.
            kind: One of dossier, notice, note.
            body: The full text.

        """
        decision = await deps.allow("draft_case_document", record=f"{kind} in {case_id}")
        person_id = person_of(decision)
        document_id = f"doc-{uuid.uuid4().hex[:16]}"
        async with database.as_person(person_id) as connection:
            case = await connection.fetchrow("select data_class from cases where id = $1", case_id)
            if case is None:
                raise AccessDeniedError("no access to this case")
            held, target = decision.context.data_class, case["data_class"]
            if _ORDER.index(held) > _ORDER.index(target):
                # No write-down: what the session has read may not flow into a less
                # sensitive case. Only a person can release it, by approving a reply.
                reason = (
                    f"this conversation holds {held} data and the case is {target}; "
                    "it cannot be written there"
                )
                await deps.control.audit(
                    kind="tool_call",
                    action="knowledge.draft_case_document",
                    outcome="denied",
                    agent_id=decision.identity.agent_id,
                    case_id=case_id,
                    system=deps.system,
                    summary=f"Refused to write {held} session content into {target} case {case_id}",
                )
                raise AccessDeniedError(reason)
            await connection.execute(
                "insert into kb_documents (id, case_id, title, kind, data_class, body, status, "
                "author_person_id) values ($1, $2, $3, $4, $5, $6, 'draft', $7)",
                document_id,
                case_id,
                title[:200],
                kind,
                target,
                body,
                person_id,
            )
        approval_id = await deps.control.create_approval(
            agent_id=decision.identity.agent_id,
            system=deps.system,
            ref=document_id,
            title=f"{kind.capitalize()}: {title}",
            body=body,
            case_id=case_id,
            detail={"kind": kind},
        )
        return {
            "status": "draft",
            "document_id": document_id,
            "approval_id": approval_id,
            "message": "Saved as a draft in the case. It is published when "
            f"{decision.identity.person_name or 'your person'} approves it.",
        }

    return [search_knowledge, get_document, draft_case_document]
