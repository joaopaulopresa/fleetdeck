import httpx
from fleetdeck_common.tokens import hash_token
from fleetdeck_control.cluster import FakeAgentCluster
from fleetdeck_control.context import AppContext
from fleetdeck_control.models import Agent
from fleetdeck_control.testing import sign_in

NEW = {"agent_id": "elena-park", "person_id": "elena-park", "bundle": "physician"}


async def test_sign_in_lists_demo_people_and_rejects_unknown(client: httpx.AsyncClient) -> None:
    people = (await client.get("/api/auth/people")).json()
    assert {p["id"] for p in people} == {
        "elena-park",
        "marcus-reyes",
        "priya-nair",
        "dana-whitfield",
    }
    assert (await client.post("/api/auth/login", json={"person_id": "nobody"})).status_code == 404
    assert (await client.get("/api/me")).status_code == 401


async def test_only_admins_manage_the_fleet(client: httpx.AsyncClient) -> None:
    employee = await sign_in(client, "marcus-reyes")
    assert (await client.get("/api/agents", headers=employee)).status_code == 403
    assert (await client.post("/api/agents", json=NEW, headers=employee)).status_code == 403


async def test_create_agent_starts_asleep_with_keys_only_in_the_cluster(
    client: httpx.AsyncClient, admin: dict[str, str], context: AppContext
) -> None:
    response = await client.post("/api/agents", json=NEW, headers=admin)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "asleep"
    assert "model_key_hash" not in body

    cluster = context.cluster
    assert isinstance(cluster, FakeAgentCluster)
    spec = cluster.specs["elena-park"]
    assert (spec.role, spec.bundle, spec.awake) == ("physician", "physician", False)
    keys = cluster.keys["elena-park"]
    async with context.db.session() as session:
        agent = await session.get(Agent, "elena-park")
    assert agent is not None
    assert agent.model_key_hash == hash_token(keys.model_key)
    assert agent.connector_token_hash == hash_token(keys.connector_token)
    assert len({keys.model_key, keys.connector_token, keys.runs_key}) == 3
    assert len(keys.runs_key) >= 16  # Hermes refuses shorter API keys


async def test_one_agent_per_person_and_valid_ids(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    assert (await client.post("/api/agents", json=NEW, headers=admin)).status_code == 201
    assert (await client.post("/api/agents", json=NEW, headers=admin)).status_code == 409
    second = {**NEW, "agent_id": "elena-two"}
    assert (await client.post("/api/agents", json=second, headers=admin)).status_code == 409
    bad = {**NEW, "agent_id": "Not_Valid", "person_id": "marcus-reyes"}
    assert (await client.post("/api/agents", json=bad, headers=admin)).status_code == 422


async def test_wake_sleep_suspend_and_delete(
    client: httpx.AsyncClient, admin: dict[str, str], context: AppContext
) -> None:
    await client.post("/api/agents", json=NEW, headers=admin)
    woke = await client.post("/api/agents/elena-park/wake", headers=admin)
    assert woke.status_code == 200
    agents = (await client.get("/api/agents", headers=admin)).json()
    assert agents[0]["state"] == "active" and agents[0]["wake_count"] == 1

    slept = await client.post("/api/agents/elena-park/sleep", headers=admin)
    assert slept.json()["state"] == "asleep"

    suspended = await client.post("/api/agents/elena-park/suspend", headers=admin)
    assert suspended.json()["state"] == "suspended"
    assert (await client.post("/api/agents/elena-park/wake", headers=admin)).status_code == 409
    resumed = await client.post("/api/agents/elena-park/resume", headers=admin)
    assert resumed.json()["state"] == "asleep"

    assert (await client.delete("/api/agents/elena-park", headers=admin)).status_code == 204
    cluster = context.cluster
    assert isinstance(cluster, FakeAgentCluster)
    assert "elena-park" not in cluster.specs and "elena-park" not in cluster.keys
    assert (await client.post("/api/agents/elena-park/wake", headers=admin)).status_code == 404


async def test_employee_sees_and_wakes_only_their_own_agent(
    client: httpx.AsyncClient, admin: dict[str, str]
) -> None:
    await client.post("/api/agents", json=NEW, headers=admin)
    elena = await sign_in(client, "elena-park")
    marcus = await sign_in(client, "marcus-reyes")
    assert (await client.get("/api/me/agent", headers=elena)).json()["id"] == "elena-park"
    assert (await client.get("/api/me/agent", headers=marcus)).status_code == 404
    woke = await client.post("/api/me/agent/wake", headers=elena)
    assert woke.status_code == 200 and woke.json()["agent_id"] == "elena-park"
