"""The connectors against the real control plane, the real mock systems and the real dataset.

Every test here is an agent trying to do something through a connector with its own token; the
interesting ones are the attempts that must be refused.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fleetdeck_common.control_client import ControlPlaneClient
from fleetdeck_common.errors import AuthenticationError
from fleetdeck_connectors import billing, contracts, docket, erp, knowledge, patient_record
from fleetdeck_connectors.base import ControlVerifier, Deps, SystemsClient
from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.context import AppContext
from fleetdeck_control.hermes import RunEvent
from fleetdeck_control.models import Approval, AuditEvent, KbDocument
from fleetdeck_control.seed import _load, seed_access, seed_cases, seed_knowledge
from fleetdeck_mock.app import create_app as create_systems
from fleetdeck_mock.settings import MockSettings
from sqlalchemy import select

SEED = Path(__file__).parents[2] / "seed"
STORY_CLAIM, OLD_DISPUTE = "CLM-2026-01187", "CLM-2026-00672"
ROLES = {
    "elena-park": "physician",
    "marcus-reyes": "biller",
    "priya-nair": "lawyer",
    "tom-becker": "tax-analyst",
    "sofia-lindqvist": "director",
}
Toolbox = dict[str, Callable[..., Awaitable[dict[str, Any]]]]


class World:
    """Five agents with their tokens, and every connector's tools for any of them."""

    def __init__(
        self,
        context: AppContext,
        control: ControlPlaneClient,
        systems: SystemsClient,
        database: knowledge.ScopedDatabase,
        tokens: dict[str, str],
    ) -> None:
        self.context, self.control, self.systems = context, control, systems
        self.database, self.tokens = database, tokens

    def tools(self, person: str, system: str) -> Toolbox:
        deps = Deps(
            system=system,
            control=self.control,
            systems=self.systems,
            token=lambda: self.tokens[person],
        )
        if system == "knowledge":
            built = knowledge.tools(deps, self.database)
        else:
            module = {
                "patient-record": patient_record,
                "billing": billing,
                "erp": erp,
                "docket": docket,
                "contracts": contracts,
            }[system]
            built = module.tools(deps)
        return {tool.__name__: tool for tool in built}

    async def last_audit(self, **filters: str) -> AuditEvent:
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
        for key, value in filters.items():
            query = query.where(getattr(AuditEvent, key) == value)
        async with self.context.db.session() as session:
            event = await session.scalar(query)
        assert event is not None
        return event


@pytest.fixture
async def world(
    context: AppContext, client: httpx.AsyncClient, database_url: str, tmp_path: Path
) -> AsyncIterator[World]:
    await seed_access(context, _load(SEED, "systems.yaml"))
    await seed_cases(context, _load(SEED, "cases.yaml"))
    await seed_knowledge(context, _load(SEED, "knowledge.json"))
    tokens = {}
    for person, bundle in ROLES.items():
        await context.agents.create(NewAgent(person, bundle, person_id=person))
        tokens[person] = (await context.cluster.read_keys(person)).connector_token
    settings = context.settings.model_copy(update={"control_plane_url": "http://test"})
    systems_app = create_systems(
        MockSettings(
            seed_dir=SEED / "data", internal_token="internal-test-token", today="2026-11-12"
        )
    )
    systems_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=systems_app))
    database = knowledge.ScopedDatabase(database_url, scoped_role="fleet_scoped")
    yield World(
        context,
        ControlPlaneClient(settings, client=client),
        SystemsClient("http://systems", "internal-test-token", client=systems_http),
        database,
        tokens,
    )
    await database.close()
    await systems_http.aclose()


async def test_the_lawyers_agent_is_refused_the_patient_record(world: World) -> None:
    for person in ("priya-nair", "marcus-reyes", "tom-becker", "sofia-lindqvist"):
        record = world.tools(person, "patient-record")
        for call in (
            record["get_patient_summary"]("PAT-0001"),
            record["get_encounter_note"]("ENC-000102"),
            record["list_my_patients"](),
        ):
            result = await call
            assert result["error"] == "access denied", (person, result)
            assert "Whitaker" not in str(result) and "LVEF" not in str(result)
    event = await world.last_audit(person_id="priya-nair", system="patient-record")
    assert event.outcome == "denied" and event.kind == "tool_call"


async def test_a_physician_reads_only_her_own_patients(world: World) -> None:
    record = world.tools("elena-park", "patient-record")
    mine = (await record["list_my_patients"]())["patients"]
    assert len(mine) == 9 and "Harold Whitaker" in {p["name"] for p in mine}
    chart = await record["get_patient_summary"]("PAT-0001")
    assert chart["name"] == "Harold Whitaker" and len(chart["encounters"]) == 3
    note = await record["get_encounter_note"]("ENC-000102")
    assert "QRS duration 158 ms" in note["note"]

    all_patients = await world.systems.get("/patient-record/patients")
    someone_elses = next(p for p in all_patients if p["id"] not in {m["id"] for m in mine})
    refused = await record["get_patient_summary"](someone_elses["id"])
    assert refused["error"] == "access denied" and someone_elses["name"] not in str(refused)
    assert (await record["get_patient_summary"]("PAT-9999"))["error"] == "not found"


async def test_reading_the_chart_makes_the_session_restricted(world: World) -> None:
    session_row = await world.context.runs.open_session("elena-park", kind="chat")
    classes: list[str] = []

    async def events(agent_id: str, runs_key: str, run_id: str):  # type: ignore[no-untyped-def]
        await world.tools("elena-park", "patient-record")["get_patient_summary"]("PAT-0001")
        _, context = await world.control.resolve(
            (await world.context.cluster.read_keys("elena-park")).model_key, "model"
        )
        classes.append(context.data_class)
        yield RunEvent("run.completed", {"event": "run.completed", "output": "done"})

    world.context.hermes.events = events  # type: ignore[method-assign]
    run = await world.context.runs.submit(
        "elena-park", "read", trigger="chat", session_id=session_row.id
    )
    await world.context.runs.wait(run.id)
    assert classes == ["restricted"]  # what the model gateway routes on


async def test_biller_prepares_an_appeal_that_changes_nothing_until_confirmed(
    world: World,
) -> None:
    desk = world.tools("marcus-reyes", "billing")
    denials = (await desk["list_denials"]("denied"))["claims"]
    assert STORY_CLAIM in {c["id"] for c in denials} and len(denials) == 4
    claim = await desk["get_claim"](STORY_CLAIM)
    assert (claim["billed_amount"], claim["denial"]["code"]) == ("$48,750.00", "CO-50")

    result = await desk["file_appeal_draft"](STORY_CLAIM, "The service meets clause 5.2.")
    assert result["status"] == "draft" and "must confirm" in result["message"]
    assert (await desk["get_claim"](STORY_CLAIM))["status"] == "denied"
    async with world.context.db.session() as session:
        approval = await session.get(Approval, result["approval_id"])
    assert approval is not None
    assert (approval.person_id, approval.system, approval.status, approval.case_id) == (
        "marcus-reyes",
        "billing",
        "pending",
        "dn-clm-2026-01187",
    )
    assert approval.body == "The service meets clause 5.2."


async def test_a_lawyer_sees_a_claim_only_through_a_case_she_belongs_to(world: World) -> None:
    desk = world.tools("priya-nair", "billing")
    assert (await desk["get_claim"](OLD_DISPUTE))["status"] == "in_dispute"
    refused = await desk["get_claim"](STORY_CLAIM)  # not handed over to her yet
    assert refused["error"] == "access denied" and "48,750" not in str(refused)
    assert (await desk["list_denials"]())["error"] == "access denied"
    assert (await desk["file_appeal_draft"](OLD_DISPUTE, "x"))["error"] == "access denied"
    matters = (await world.tools("priya-nair", "docket")["list_matters"]())["matters"]
    assert [m["id"] for m in matters] == ["LM-2026-014"]
    refused = await world.tools("priya-nair", "docket")["open_matter_draft"](
        STORY_CLAIM, "Northwind", "Northwind Mutual Insurance"
    )
    assert refused["error"] == "access denied"


async def test_deadlines_and_tax_come_from_code_with_their_sources(world: World) -> None:
    due = await world.tools("priya-nair", "docket")["compute_deadline"](
        "notice_of_dispute", "2026-11-12"
    )
    assert due["due_date"] == "2026-12-14" and "clause 12.2" in due["source"]
    tax = await world.tools("tom-becker", "erp")["run_tax_engine"]("2026-09")
    assert tax["income_tax_provision"] == "$27,075.30" and "computed in code" in tax["source"]
    flagged = await world.tools("tom-becker", "erp")["flag_disputed_draft"](
        STORY_CLAIM, "Formal dispute"
    )
    assert flagged["status"] == "draft"
    entry = await world.tools("tom-becker", "erp")["get_revenue_entry"](STORY_CLAIM)
    assert entry["status"] == "recognized"  # the draft posted nothing
    for person in ("marcus-reyes", "priya-nair", "elena-park"):
        assert (await world.tools(person, "erp")["run_tax_engine"]("2026-09"))["error"] == (
            "access denied"
        )


async def test_the_director_gets_totals_with_sources_but_not_the_records(world: World) -> None:
    summary = await world.tools("sofia-lindqvist", "billing")["denial_summary"]()
    assert summary["total_amount"] == "$163,745.00"
    assert STORY_CLAIM in next(s for s in summary["stages"] if s["stage"] == "denied")["claim_ids"]
    matters = await world.tools("sofia-lindqvist", "docket")["docket_summary"]()
    assert matters["open_matters"] == 1 and matters["matters"][0]["next_deadline"] == "2026-11-23"
    assert (await world.tools("sofia-lindqvist", "billing")["get_claim"](STORY_CLAIM))[
        "error"
    ] == "access denied"
    clause = await world.tools("sofia-lindqvist", "contracts")["get_clause"](
        "CTR-NORTHWIND-2025", "12.2"
    )
    assert "thirty (30) calendar days" in clause["text"]
    assert (await world.tools("elena-park", "contracts")["search_contracts"]("Northwind"))[
        "error"
    ] == "access denied"


async def test_knowledge_search_is_filtered_by_the_database(world: World) -> None:
    def ids(result: dict[str, Any]) -> set[str]:
        return {d["id"] for d in result["documents"]}

    biller = world.tools("marcus-reyes", "knowledge")
    lawyer = world.tools("priya-nair", "knowledge")
    assert "kb-appeal-letter-template" in ids(await biller["search_knowledge"]("appeal template"))
    assert "kb-notice-of-dispute-template" in ids(
        await lawyer["search_knowledge"]("notice dispute")
    )
    assert "kb-notice-of-dispute-template" not in ids(
        await biller["search_knowledge"]("notice dispute")
    )
    assert (await biller["get_document"]("kb-notice-of-dispute-template"))["error"] == "not found"
    assert "Structure" in (await biller["get_document"]("kb-appeal-letter-template"))["body"]


async def test_case_documents_are_drafts_and_only_members_can_write_them(world: World) -> None:
    case_id = "dn-clm-2026-01187"
    saved = await world.tools("marcus-reyes", "knowledge")["draft_case_document"](
        case_id, "Dossier for Legal", "dossier", "Claim history and amounts."
    )
    assert saved["status"] == "draft"
    async with world.context.db.session() as session:
        document = await session.get(KbDocument, saved["document_id"])
        approval = await session.get(Approval, saved["approval_id"])
    assert document is not None and approval is not None
    assert (document.status, document.author_person_id) == ("draft", "marcus-reyes")
    assert (approval.person_id, approval.system) == ("marcus-reyes", "knowledge")

    for outsider in ("priya-nair", "elena-park"):
        refused = await world.tools(outsider, "knowledge")["draft_case_document"](
            case_id, "x", "note", "y"
        )
        assert refused == {"error": "access denied", "reason": "no access to this case"}
        found = await world.tools(outsider, "knowledge")["search_knowledge"]("Dossier", case_id)
        assert found["documents"] == []


async def test_restricted_session_content_cannot_be_written_into_a_confidential_case(
    world: World,
) -> None:
    """No write-down: after reading a chart, the agent cannot file text into the dispute case."""
    async with world.context.db.session() as session:
        from fleetdeck_control.models import CaseMember

        session.add(CaseMember(case_id="dn-clm-2026-01187", person_id="elena-park"))
    session_row = await world.context.runs.open_session("elena-park", kind="chat")
    results: list[dict[str, Any]] = []

    async def events(agent_id: str, runs_key: str, run_id: str):  # type: ignore[no-untyped-def]
        await world.tools("elena-park", "patient-record")["get_patient_summary"]("PAT-0001")
        results.append(
            await world.tools("elena-park", "knowledge")["draft_case_document"](
                "dn-clm-2026-01187", "Chart copy", "note", "LVEF 28%, full history ..."
            )
        )
        results.append(
            await world.tools("elena-park", "knowledge")["draft_case_document"](
                "ep-pat-0001", "Visit plan", "note", "Follow-up in two weeks."
            )
        )
        yield RunEvent("run.completed", {"event": "run.completed", "output": "done"})

    world.context.hermes.events = events  # type: ignore[method-assign]
    run = await world.context.runs.submit(
        "elena-park", "copy", trigger="chat", session_id=session_row.id
    )
    await world.context.runs.wait(run.id)
    refused, allowed = results
    assert refused["error"] == "access denied" and "restricted" in refused["reason"]
    assert allowed["status"] == "draft"  # same class as the session: fine
    event = await world.last_audit(action="knowledge.draft_case_document", outcome="denied")
    assert event.person_id == "elena-park" and event.case_id == "dn-clm-2026-01187"


async def test_a_forged_or_suspended_token_gets_nowhere(world: World) -> None:
    verifier = ControlVerifier(world.control)
    assert await verifier.verify_token("fdc_forged") is None
    good = await verifier.verify_token(world.tokens["marcus-reyes"])
    assert good is not None and good.client_id == "marcus-reyes"

    world.tokens["ghost"] = "fdc_forged"
    with pytest.raises(AuthenticationError):
        await world.tools("ghost", "billing")["list_denials"]()
    await world.context.agents.set_suspended("marcus-reyes", True, actor=None)
    assert await verifier.verify_token(world.tokens["marcus-reyes"]) is None
    with pytest.raises(AuthenticationError):
        await world.tools("marcus-reyes", "billing")["list_denials"]()
