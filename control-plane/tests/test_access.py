"""Role permissions, case membership and row-level security, including the refusals."""

from typing import Any

import httpx
import pytest
from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import AgentSession, AuditEvent, Case, KbDocument
from fleetdeck_control.seed import seed_access, seed_cases, seed_knowledge
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from .conftest import FakeConfirmer, sign_in

INTERNAL = {"Authorization": "Bearer internal-test-token"}
SYSTEMS = [
    {
        "name": "patient-record",
        "title": "Patient record",
        "description": "",
        "data_class": "restricted",
        "tools": {"get_patient_summary": {"physician": "case"}},
    },
    {
        "name": "billing",
        "title": "Billing",
        "description": "",
        "data_class": "confidential",
        "tools": {
            "list_denials": {"biller": "all"},
            "get_claim": {"biller": "all", "lawyer": "case"},
        },
    },
]
CASES = [
    {
        "id": "case-episode",
        "kind": "patient_episode",
        "title": "Episode of H. W.",
        "summary": "",
        "data_class": "restricted",
        "members": {"elena-park": "owner"},
    },
    {
        "id": "case-dispute",
        "kind": "claim_dispute",
        "title": "Denied claim CLM-1",
        "summary": "",
        "data_class": "confidential",
        "members": {"marcus-reyes": "owner", "priya-nair": "member"},
    },
]
DOCS = [
    {
        "id": "doc-appeal-template",
        "case_id": None,
        "title": "Appeal letter template",
        "kind": "template",
        "data_class": "general",
        "audience_roles": [],
        "body": "Dear payer",
    },
    {
        "id": "doc-litigation-playbook",
        "case_id": None,
        "title": "Litigation playbook",
        "kind": "protocol",
        "data_class": "confidential",
        "audience_roles": ["lawyer"],
        "body": "Privileged strategy",
    },
    {
        "id": "doc-dossier",
        "case_id": "case-dispute",
        "title": "Dispute dossier",
        "kind": "dossier",
        "data_class": "confidential",
        "audience_roles": [],
        "body": "Claim CLM-1 timeline",
    },
]


@pytest.fixture
async def world(context: AppContext) -> dict[str, str]:
    """Three agents, two systems, two cases, three documents. Returns connector tokens."""
    await seed_access(context, SYSTEMS)
    await seed_cases(context, CASES)
    await seed_knowledge(context, DOCS)
    tokens = {}
    for agent_id, bundle in (
        ("elena-park", "physician"),
        ("marcus-reyes", "biller"),
        ("priya-nair", "lawyer"),
    ):
        await context.agents.create(NewAgent(agent_id, bundle, person_id=agent_id))
        tokens[agent_id] = (await context.cluster.read_keys(agent_id)).connector_token
    return tokens


async def authorize(
    client: httpx.AsyncClient, token: str, system: str, tool: str, case_id: str | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/internal/authorize",
        headers=INTERNAL,
        json={
            "token": token,
            "system": system,
            "tool": tool,
            "case_id": case_id,
            "record": "rec-1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_role_decides_which_tools_exist_for_an_agent(
    client: httpx.AsyncClient, world: dict[str, str]
) -> None:
    assert (await authorize(client, world["marcus-reyes"], "billing", "list_denials"))["allowed"]
    # The lawyer and the biller have no patient-record tool at all; the physician no billing.
    for who in ("priya-nair", "marcus-reyes"):
        refused = await authorize(
            client, world[who], "patient-record", "get_patient_summary", "case-episode"
        )
        assert refused["allowed"] is False and "may not use" in refused["reason"]
    refused = await authorize(client, world["elena-park"], "billing", "list_denials")
    assert refused["allowed"] is False
    unknown = await authorize(client, world["marcus-reyes"], "billing", "delete_everything")
    assert unknown["allowed"] is False


async def test_case_scoped_tools_need_membership_and_reveal_nothing_to_outsiders(
    client: httpx.AsyncClient, world: dict[str, str]
) -> None:
    own = await authorize(
        client, world["elena-park"], "patient-record", "get_patient_summary", "case-episode"
    )
    assert own["allowed"] is True and own["identity"]["person_id"] == "elena-park"

    lawyer_in = await authorize(client, world["priya-nair"], "billing", "get_claim", "case-dispute")
    assert lawyer_in["allowed"] is True
    answers = [
        await authorize(client, world["priya-nair"], "billing", "get_claim", "case-episode"),
        await authorize(client, world["priya-nair"], "billing", "get_claim", "case-nonexistent"),
        await authorize(client, world["priya-nair"], "billing", "get_claim", None),
    ]
    assert all(a["allowed"] is False for a in answers)
    # An existing case she is not in is indistinguishable from one that does not exist.
    assert len({a["reason"] for a in answers}) == 1


async def test_unknown_token_and_suspended_agent_are_refused(
    client: httpx.AsyncClient, world: dict[str, str], context: AppContext
) -> None:
    body = {"token": "fdc_forged", "system": "billing", "tool": "list_denials"}
    assert (
        await client.post("/internal/authorize", headers=INTERNAL, json=body)
    ).status_code == 401
    await context.agents.set_suspended("marcus-reyes", True, actor=None)
    body["token"] = world["marcus-reyes"]
    assert (
        await client.post("/internal/authorize", headers=INTERNAL, json=body)
    ).status_code == 401


async def test_every_decision_is_audited_and_restricted_data_marks_the_session(
    client: httpx.AsyncClient, world: dict[str, str], context: AppContext
) -> None:
    session_row = await context.runs.open_session("elena-park", kind="chat")
    seen: list[str] = []

    async def events(agent_id: str, runs_key: str, run_id: str):  # type: ignore[no-untyped-def]
        from fleetdeck_control.hermes import RunEvent

        decision = await authorize(
            client, world["elena-park"], "patient-record", "get_patient_summary", "case-episode"
        )
        seen.append(decision["context"]["run_id"])
        yield RunEvent("run.completed", {"event": "run.completed", "output": "done"})

    context.hermes.events = events  # type: ignore[method-assign]
    run = await context.runs.submit(
        "elena-park", "summarise the record", trigger="chat", session_id=session_row.id
    )
    await context.runs.wait(run.id)
    await authorize(
        client, world["priya-nair"], "patient-record", "get_patient_summary", "case-episode"
    )

    async with context.db.session() as session:
        stored = await session.get(AgentSession, session_row.id)
        events_ = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.system == "patient-record")
                .order_by(AuditEvent.id.desc())
                .limit(3)
            )
        ).all()
    assert seen == [run.id]
    assert stored is not None and stored.data_class == "restricted"
    denied, raised_or_allowed = events_[0], {e.action: e for e in events_[1:]}
    assert (denied.outcome, denied.person_id, denied.kind) == ("denied", "priya-nair", "tool_call")
    assert "was refused patient-record.get_patient_summary" in denied.summary
    allowed = raised_or_allowed["patient-record.get_patient_summary"]
    assert (allowed.outcome, allowed.run_id, allowed.case_id) == ("allowed", run.id, "case-episode")
    assert "session.data_class_raised" in raised_or_allowed


async def test_people_only_see_their_own_cases(
    client: httpx.AsyncClient, world: dict[str, str]
) -> None:
    marcus = await sign_in(client, "marcus-reyes")
    elena = await sign_in(client, "elena-park")
    assert [c["id"] for c in (await client.get("/api/me/cases", headers=marcus)).json()] == [
        "case-dispute"
    ]
    assert [c["id"] for c in (await client.get("/api/me/cases", headers=elena)).json()] == [
        "case-episode"
    ]
    detail = (await client.get("/api/cases/case-dispute", headers=marcus)).json()
    assert {m["person_id"] for m in detail["members"]} == {"marcus-reyes", "priya-nair"}
    assert [d["id"] for d in detail["documents"]] == ["doc-dossier"]
    # To Elena the dispute does not exist: not the case, not its document.
    assert (await client.get("/api/cases/case-dispute", headers=elena)).status_code == 404
    assert (await client.get("/api/documents/doc-dossier", headers=elena)).status_code == 404


async def test_shared_documents_follow_their_audience(
    client: httpx.AsyncClient, world: dict[str, str]
) -> None:
    marcus = await sign_in(client, "marcus-reyes")
    priya = await sign_in(client, "priya-nair")
    assert (
        await client.get("/api/documents/doc-appeal-template", headers=marcus)
    ).status_code == 200
    assert (
        await client.get("/api/documents/doc-litigation-playbook", headers=priya)
    ).status_code == 200
    assert (
        await client.get("/api/documents/doc-litigation-playbook", headers=marcus)
    ).status_code == 404


async def test_row_level_security_holds_in_the_database_itself(
    world: dict[str, str], context: AppContext
) -> None:
    """The same checks without the API: straight SQL as the scoped role."""
    async with context.db.session(person_id="elena-park") as session:
        assert (await session.scalars(select(Case.id))).all() == ["case-episode"]
        assert (await session.scalars(select(KbDocument.id).order_by(KbDocument.id))).all() == [
            "doc-appeal-template"
        ]
        members = (await session.execute(text("select person_id from case_members"))).all()
        assert members == [("elena-park",)]

    # A scoped connection that names no person sees no case and no case document.
    async with context.db.session() as session:
        await session.execute(text("set local role fleet_scoped"))
        assert (await session.scalars(select(Case.id))).all() == []
        assert (await session.scalars(select(KbDocument.id))).all() == ["doc-appeal-template"]

    # Setting someone else's id is all it takes to be them, which is why only platform
    # services hold a database login; the role itself cannot touch other tables.
    for statement in (
        "select * from agents",
        "select * from audit_events",
        "update cases set title = 'x'",
        "delete from kb_documents",
        "insert into case_members (case_id, person_id) values ('case-dispute', 'elena-park')",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with context.db.session(person_id="elena-park") as session:
                await session.execute(text(statement))


async def test_scoped_role_can_only_add_its_own_drafts_to_its_own_cases(
    world: dict[str, str], context: AppContext
) -> None:
    def draft(case_id: str, status: str = "draft", author: str = "elena-park") -> KbDocument:
        return KbDocument(
            id=f"doc-{case_id}-{status}-{author}",
            case_id=case_id,
            title="t",
            kind="note",
            body="b",
            status=status,
            author_person_id=author,
            audience_roles=[],
        )

    async with context.db.session(person_id="elena-park") as session:
        session.add(draft("case-episode"))
    for bad in (
        draft("case-dispute"),
        draft("case-episode", status="published"),
        draft("case-episode", author="marcus-reyes"),
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with context.db.session(person_id="elena-park") as session:
                session.add(bad)


async def test_drafts_wait_for_their_owner_and_only_the_owner_decides(
    client: httpx.AsyncClient, world: dict[str, str], context: AppContext
) -> None:
    created = await client.post(
        "/internal/approvals",
        headers=INTERNAL,
        json={
            "agent_id": "marcus-reyes",
            "system": "billing",
            "ref": "draft-7",
            "title": "Appeal for claim CLM-1",
            "body": "We appeal.",
            "case_id": "case-dispute",
        },
    )
    assert created.status_code == 200
    approval_id = created.json()["id"]
    marcus = await sign_in(client, "marcus-reyes")
    priya = await sign_in(client, "priya-nair")

    waiting = (await client.get("/api/me/approvals", headers=marcus)).json()
    assert [(a["id"], a["system"], a["status"]) for a in waiting] == [
        (approval_id, "billing", "pending")
    ]
    assert (await client.get("/api/me/approvals", headers=priya)).json() == []
    stolen = await client.post(f"/api/approvals/{approval_id}/approve", json={}, headers=priya)
    assert stolen.status_code == 403
    confirmer = context.approvals._confirmer
    assert isinstance(confirmer, FakeConfirmer) and confirmer.confirmed == []

    approved = await client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=marcus,
        json={"body": "We appeal, with the attached justification."},
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert confirmer.confirmed == [
        ("billing", "draft-7", "marcus-reyes", "We appeal, with the attached justification.")
    ]
    again = await client.post(f"/api/approvals/{approval_id}/approve", json={}, headers=marcus)
    assert again.status_code == 409


async def test_declining_discards_the_draft(
    client: httpx.AsyncClient, world: dict[str, str], context: AppContext
) -> None:
    created = await client.post(
        "/internal/approvals",
        headers=INTERNAL,
        json={"agent_id": "marcus-reyes", "system": "billing", "ref": "draft-8", "title": "Appeal"},
    )
    marcus = await sign_in(client, "marcus-reyes")
    declined = await client.post(f"/api/approvals/{created.json()['id']}/decline", headers=marcus)
    assert declined.json()["status"] == "declined"
    confirmer = context.approvals._confirmer
    assert isinstance(confirmer, FakeConfirmer)
    assert confirmer.discarded == [("billing", "draft-8")] and confirmer.confirmed == []


async def test_admin_changes_permissions_and_they_apply_at_once(
    client: httpx.AsyncClient, world: dict[str, str], admin: dict[str, str]
) -> None:
    systems = (await client.get("/api/admin/systems", headers=admin)).json()
    billing = next(s for s in systems if s["name"] == "billing")
    assert {"tool": "get_claim", "role": "lawyer", "scope": "case"} in billing["permissions"]
    marcus = await sign_in(client, "marcus-reyes")
    assert (await client.get("/api/admin/systems", headers=marcus)).status_code == 403

    rows = [p for p in billing["permissions"] if p["role"] != "lawyer"]
    changed = await client.put("/api/admin/systems/billing/permissions", json=rows, headers=admin)
    assert changed.status_code == 200
    refused = await authorize(client, world["priya-nair"], "billing", "get_claim", "case-dispute")
    assert refused["allowed"] is False

    cases = (await client.get("/api/admin/cases", headers=admin)).json()
    dispute = next(c for c in cases if c["id"] == "case-dispute")
    assert dispute["members"] == ["marcus-reyes", "priya-nair"] and dispute["documents"] == 1
    assert "body" not in dispute
