"""The mock systems against the committed dataset."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fleetdeck_mock.app import create_app
from fleetdeck_mock.settings import MockSettings

SEED = Path(__file__).parents[2] / "seed" / "data"
CLAIM = "CLM-2026-01187"
PLATFORM = {"Authorization": "Bearer internal"}


@pytest.fixture
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = MockSettings(
        seed_dir=SEED,
        internal_token="internal",
        today="2026-11-12",
        state_file=str(tmp_path / "state.json"),
    )
    with TestClient(create_app(settings), headers=PLATFORM) as client:
        yield client


def test_only_platform_services_get_in(api: TestClient) -> None:
    assert (
        api.get("/billing/claims", headers={"Authorization": "Bearer agent-token"}).status_code
        == 401
    )
    assert (
        api.get("/patient-record/patients/PAT-0001", headers={"Authorization": ""}).status_code
        == 401
    )
    assert api.get("/healthz", headers={"Authorization": ""}).json()["today"] == "2026-11-12"


def test_storyline_records_are_in_the_dataset(api: TestClient) -> None:
    claim = api.get(f"/billing/claims/{CLAIM}").json()
    assert (claim["status"], claim["billed_amount"], claim["payer"]) == (
        "denied",
        "$48,750.00",
        "Northwind Mutual Insurance",
    )
    assert claim["denial"]["code"] == "CO-50" and claim["case_id"] == "dn-clm-2026-01187"
    chart = api.get("/patient-record/patients/PAT-0001").json()
    assert chart["case_id"] == "ep-pat-0001" and len(chart["encounters"]) == 3
    note = api.get("/patient-record/encounters/ENC-000102").json()
    assert "QRS duration 158 ms" in note["note"] and note["case_id"] == "ep-pat-0001"
    clause = api.get("/contracts/CTR-NORTHWIND-2025/clauses/12.2").json()
    assert "thirty (30) calendar days" in clause["text"]
    hits = api.get("/contracts", params={"query": "Northwind notice of dispute"}).json()
    assert hits[0]["contract_id"] == "CTR-NORTHWIND-2025"


def test_an_appeal_is_only_a_draft_until_a_person_confirms_it(api: TestClient) -> None:
    draft = api.post(
        "/billing/drafts/appeal",
        json={"author": "marcus-reyes", "claim_id": CLAIM, "justification": "Meets criteria."},
    ).json()
    assert draft["status"] == "draft" and draft["payload"]["level"] == 1
    assert api.get(f"/billing/claims/{CLAIM}").json()["status"] == "denied"  # nothing filed

    confirmed = api.post(
        f"/billing/drafts/{draft['id']}/confirm",
        json={"person_id": "marcus-reyes", "body": "Meets criteria; see justification."},
    )
    assert confirmed.status_code == 200
    claim = api.get(f"/billing/claims/{CLAIM}").json()
    assert claim["status"] == "appealed"
    assert claim["appeals"][0] == {
        "level": 1,
        "filed_date": "2026-11-12",
        "filed_by": "marcus-reyes",
        "justification": "Meets criteria; see justification.",
        "outcome": None,
    }
    again = api.post(f"/billing/drafts/{draft['id']}/confirm", json={"person_id": "marcus-reyes"})
    assert again.status_code == 409
    wrong_system = api.post(f"/erp/drafts/{draft['id']}/confirm", json={"person_id": "x"})
    assert wrong_system.status_code == 404


def test_a_discarded_draft_changes_nothing(api: TestClient) -> None:
    draft = api.post(
        "/erp/drafts/flag-disputed",
        json={"author": "tom-becker", "claim_id": CLAIM, "reason": "Dispute"},
    ).json()
    api.post(f"/erp/drafts/{draft['id']}/discard", json={"person_id": "tom-becker"})
    assert api.get(f"/erp/revenue/{CLAIM}").json()["status"] == "recognized"
    late = api.post(f"/erp/drafts/{draft['id']}/confirm", json={"person_id": "tom-becker"})
    assert late.status_code == 409


def test_the_whole_dispute_path_moves_the_numbers(api: TestClient) -> None:
    def confirm(system: str, draft: dict[str, str], person: str) -> None:
        assert (
            api.post(
                f"/{system}/drafts/{draft['id']}/confirm", json={"person_id": person}
            ).status_code
            == 200
        )

    before = api.get("/billing/denial-summary").json()
    tax_before = api.get("/erp/tax-engine", params={"period": "2026-09"}).json()

    confirm(
        "billing",
        api.post(
            "/billing/drafts/appeal",
            json={"author": "marcus-reyes", "claim_id": CLAIM, "justification": "j"},
        ).json(),
        "marcus-reyes",
    )
    decided = api.post("/billing/simulate/appeal-decision", json={"claim_id": CLAIM}).json()
    assert decided["status"] == "appeal_denied" and decided["appeals"][0]["outcome"] == "upheld"

    confirm(
        "docket",
        api.post(
            "/docket/drafts/open-case",
            json={
                "author": "priya-nair",
                "claim_id": CLAIM,
                "title": "Northwind: CLM-2026-01187",
                "counterparty": "Northwind Mutual Insurance",
            },
        ).json(),
        "priya-nair",
    )
    matter = next(m for m in api.get("/docket/cases").json() if m["claim_id"] == CLAIM)
    assert matter["case_id"] == "dn-clm-2026-01187"
    assert api.get(f"/billing/claims/{CLAIM}").json()["status"] == "in_dispute"

    confirm(
        "docket",
        api.post(
            "/docket/drafts/formal-notice",
            json={"author": "priya-nair", "legal_case_id": matter["id"], "text": "Notice."},
        ).json(),
        "priya-nair",
    )
    deadlines = api.get(f"/docket/cases/{matter['id']}").json()["deadlines"]
    # 45 calendar days after 12 Nov 2026 is Sunday 27 Dec: due Monday 28 Dec.
    assert [(d["rule"], d["due_date"]) for d in deadlines] == [("notice_response", "2026-12-28")]

    confirm(
        "erp",
        api.post(
            "/erp/drafts/flag-disputed",
            json={
                "author": "tom-becker",
                "claim_id": CLAIM,
                "reason": f"Formal dispute {matter['id']}",
            },
        ).json(),
        "tom-becker",
    )
    tax_after = api.get("/erp/tax-engine", params={"period": "2026-09"}).json()
    assert tax_before["disputed_revenue"] == "$0.00"
    assert tax_after["disputed_revenue"] == "$48,750.00"
    assert tax_after["taxable_revenue"] == "$80,180.00"
    assert tax_after["income_tax_provision"] == "$16,837.80"

    after = api.get("/billing/denial-summary").json()
    stage = {s["stage"]: s for s in after["stages"]}
    assert stage["in_dispute"]["claims"] == 2 and stage["in_dispute"]["amount"] == "$109,950.00"
    assert after["total_amount"] == before["total_amount"] == "$163,745.00"
    summary = api.get("/docket/summary").json()
    assert summary["open_matters"] == 2

    # State survives a restart of the service, and a reset brings the seed back.
    assert (
        api.get("/erp/month-end", params={"period": "2026-09"}).json()["exceptions"][0]["claim_id"]
        == CLAIM
    )
    api.post("/admin/reset")
    assert api.get(f"/billing/claims/{CLAIM}").json()["status"] == "denied"
