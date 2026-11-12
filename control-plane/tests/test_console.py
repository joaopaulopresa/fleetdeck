"""Console endpoints: usage and cost, capacity, settings, skills and demo reset."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fleetdeck_control import capacity
from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.cluster import FakeAgentCluster
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import AgentRequest, Case
from fleetdeck_control.seed import _load, seed_cases
from fleetdeck_control.testing import sign_in

SEED = Path(__file__).parents[2] / "seed"
INTERNAL = {"Authorization": "Bearer internal-test-token"}


async def _agents(context: AppContext) -> dict[str, str]:
    tokens = {}
    for person, bundle in (("elena-park", "physician"), ("marcus-reyes", "biller")):
        await context.agents.create(NewAgent(person, bundle, person_id=person))
        tokens[person] = (await context.cluster.read_keys(person)).connector_token
    return tokens


async def test_usage_is_broken_down_by_person_department_model_and_data_class(
    client: httpx.AsyncClient, context: AppContext, admin: dict[str, str]
) -> None:
    await _agents(context)
    calls = [
        ("elena-park", "restricted", "restricted", "inhouse/model", 1000, 50, 0.002),
        ("elena-park", "default", "general", "deepseek/x", 2000, 100, 0.001),
        ("marcus-reyes", "default", "confidential", "deepseek/x", 4000, 200, 0.003),
    ]
    for agent, route, data_class, model, tokens_in, tokens_out, cost in calls:
        await client.post(
            "/internal/model-calls",
            headers=INTERNAL,
            json={
                "agent_id": agent,
                "route": route,
                "data_class": data_class,
                "model": model,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cost_usd": cost,
            },
        )
    usage = (await client.get("/api/admin/usage", headers=admin)).json()
    assert usage["totals"]["calls"] == 3 and usage["totals"]["input_tokens"] == 7000
    assert usage["totals"]["cost_usd"] == pytest.approx(0.006)
    by_person = {row["person_id"]: row for row in usage["by_person"]}
    assert by_person["elena-park"]["name"] == "Dr. Elena Park"
    assert by_person["elena-park"]["cost_usd"] == pytest.approx(0.003)
    assert {row["department"] for row in usage["by_department"]} == {"Clinical", "Revenue Cycle"}
    assert {(r["model"], r["route"]) for r in usage["by_model"]} == {
        ("inhouse/model", "restricted"),
        ("deepseek/x", "default"),
    }
    assert {r["data_class"]: r["calls"] for r in usage["by_data_class"]} == {
        "restricted": 1,
        "general": 1,
        "confidential": 1,
    }
    assert len(usage["daily"]) == 1 and usage["daily"][0]["calls"] == 3
    elena = await sign_in(client, "elena-park")
    assert (await client.get("/api/admin/usage", headers=elena)).status_code == 403


def test_capacity_estimate_follows_the_share_of_active_agents() -> None:
    gib = 1024**3
    result = capacity.estimate(node_memory=16 * gib, memory_per_active_agent=320 * 1024**2)
    assert result["always_on_agents"] == 40  # 80% of 16 GiB / 320 MiB
    assert {row["active_share"]: row["employees"] for row in result["by_active_share"]} == {
        0.05: 800,
        0.1: 400,
        0.2: 200,
        0.5: 80,
        1.0: 40,
    }
    assert capacity.estimate(0, 1)["always_on_agents"] is None


async def test_capacity_reports_states_and_cold_starts_without_prometheus(
    client: httpx.AsyncClient, context: AppContext, admin: dict[str, str]
) -> None:
    await _agents(context)
    await context.activator.ensure_awake("elena-park", trigger="chat")
    report = (await client.get("/api/admin/capacity", headers=admin)).json()
    assert report["agents"] == {"total": 2, "active": 1, "asleep": 1}
    assert report["cold_starts"]["count"] == 1
    assert report["cold_starts"]["recent"][0]["agent_id"] == "elena-park"
    assert report["measured"] is False and "configured memory request" in report["memory_source"]


async def test_idle_limit_is_a_runtime_setting_the_reaper_obeys(
    client: httpx.AsyncClient, context: AppContext, admin: dict[str, str]
) -> None:
    await _agents(context)
    await context.activator.ensure_awake("elena-park", trigger="chat")
    soon = datetime.now(UTC) + timedelta(seconds=45)
    assert await context.reaper.reap_once(now=soon) == []  # the configured limit is 60 s
    changed = await client.put("/api/admin/settings", json={"idle_seconds": 30}, headers=admin)
    assert changed.status_code == 200
    assert (
        await client.put("/api/admin/settings", json={"idle_seconds": 5}, headers=admin)
    ).status_code == 422
    assert await context.reaper.reap_once(now=soon) == ["elena-park"]
    elena = await sign_in(client, "elena-park")
    assert (await client.get("/api/config", headers=elena)).json()["idle_seconds"] == 30


async def test_a_personal_skill_needs_its_owner_and_a_shared_one_needs_an_administrator(
    client: httpx.AsyncClient, context: AppContext, admin: dict[str, str]
) -> None:
    tokens = await _agents(context)
    cluster = context.cluster
    assert isinstance(cluster, FakeAgentCluster)

    async def propose(name: str, share: bool) -> str:
        response = await client.post(
            "/internal/fleet/propose-skill",
            headers=INTERNAL,
            json={
                "token": tokens["marcus-reyes"],
                "name": name,
                "description": "When calling a payer",
                "body": "1. Note the call reference.",
                "share": share,
            },
        )
        assert response.status_code == 200, response.text
        return str(response.json()["skill_id"])

    personal, shared = (
        await propose("payer-call-notes", False),
        await propose("payer-escalation", True),
    )
    bad = await client.post(
        "/internal/fleet/propose-skill",
        headers=INTERNAL,
        json={
            "token": tokens["marcus-reyes"],
            "name": "Bad Name!",
            "description": "x",
            "body": "y",
        },
    )
    assert bad.status_code == 422
    assert cluster.specs["marcus-reyes"].extra_skills is None  # proposing changes nothing

    marcus = await sign_in(client, "marcus-reyes")
    elena = await sign_in(client, "elena-park")
    mine = {s["name"]: s for s in (await client.get("/api/me/skills", headers=marcus)).json()}
    assert mine["payer-call-notes"]["status"] == "proposed"
    assert mine["appeal-letter"]["scope"] == "bundle"  # from his role bundle
    assert "payer-call-notes" not in {
        s["name"] for s in (await client.get("/api/me/skills", headers=elena)).json()
    }

    # Wrong deciders are refused.
    assert (
        await client.post(f"/api/me/skills/{personal}/approve", headers=elena)
    ).status_code == 403
    assert (
        await client.post(f"/api/me/skills/{shared}/approve", headers=marcus)
    ).status_code == 403
    assert (
        await client.post(f"/api/admin/skills/{personal}/approve", json={}, headers=admin)
    ).status_code == 403

    assert (
        await client.post(f"/api/me/skills/{personal}/approve", headers=marcus)
    ).status_code == 200
    assert [s[0] for s in cluster.specs["marcus-reyes"].extra_skills or ()] == ["payer-call-notes"]
    assert cluster.specs["elena-park"].extra_skills == ()

    approved = await client.post(
        f"/api/admin/skills/{shared}/approve",
        json={"roles": ["biller", "physician"]},
        headers=admin,
    )
    assert approved.json()["roles"] == ["biller", "physician"]
    assert [s[0] for s in cluster.specs["elena-park"].extra_skills or ()] == ["payer-escalation"]
    assert [s[0] for s in cluster.specs["marcus-reyes"].extra_skills or ()] == [
        "payer-call-notes",
        "payer-escalation",
    ]
    again = await client.post(f"/api/admin/skills/{shared}/reject", headers=admin)
    assert again.status_code == 409
    # Suspending the agent later does not drop its skills.
    await context.agents.set_suspended("marcus-reyes", True, actor=None)
    assert len(cluster.specs["marcus-reyes"].extra_skills or ()) == 2
    library = (await client.get("/api/admin/skills", headers=admin)).json()
    assert {"payer-escalation", "appeal-letter", "clinical-justification"} <= {
        s["name"] for s in library
    }


async def test_people_list_shows_who_has_an_agent(
    client: httpx.AsyncClient, context: AppContext, admin: dict[str, str]
) -> None:
    await _agents(context)
    people = {
        p["id"]: p["agent_id"]
        for p in (await client.get("/api/admin/people", headers=admin)).json()
    }
    assert people["elena-park"] == "elena-park" and people["priya-nair"] is None
    bundles = (await client.get("/api/admin/bundles", headers=admin)).json()
    assert {b["name"] for b in bundles} == {
        "physician",
        "biller",
        "lawyer",
        "tax-analyst",
        "director",
    }
    assert "billing" in next(b for b in bundles if b["name"] == "biller")["connectors"]
    unknown = await client.post(
        "/api/agents",
        headers=admin,
        json={"agent_id": "priya-nair", "person_id": "priya-nair", "bundle": "astronaut"},
    )
    assert unknown.status_code == 404


async def test_demo_reset_restores_cases_and_clears_collaboration_state(
    client: httpx.AsyncClient,
    context: AppContext,
    admin: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleetdeck_control.api import admin as admin_api

    called: list[str] = []

    async def fake_systems(ctx: object, path: str, body: dict[str, object]) -> dict[str, object]:
        called.append(path)
        return {"status": "ok"}

    monkeypatch.setattr(admin_api, "_systems", fake_systems)
    await _agents(context)
    await seed_cases(context, _load(SEED, "cases.yaml"))
    async with context.db.session() as session:
        session.add(
            AgentRequest(
                id="req_x",
                type="notify",
                from_agent="marcus-reyes",
                to_agent="elena-park",
                case_id="dn-clm-2026-01187",
                subject="s",
                body="b",
                status="delivered",
            )
        )
        case = await session.get(Case, "dn-clm-2026-01187")
        assert case is not None
        case.title = "changed during the demo"

    assert (await client.post("/api/admin/demo/reset", headers=admin)).status_code == 200
    assert called == ["/admin/reset"]
    async with context.db.session() as session:
        assert await session.get(AgentRequest, "req_x") is None
        case = await session.get(Case, "dn-clm-2026-01187")
    assert case is not None and case.title.startswith("Denied claim CLM-2026-01187")
    decided = await client.post(
        "/api/admin/demo/appeal-decision", headers=admin, json={"claim_id": "CLM-2026-01187"}
    )
    assert decided.status_code == 200 and called[-1] == "/billing/simulate/appeal-decision"
