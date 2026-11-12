"""Sleeping and waking a real agent pod: the pod goes away, its state does not."""

import pytest

from .conftest import Api, kubectl, run_in_agent

pytestmark = pytest.mark.cluster

WRITE = "open('/opt/data/marker.txt', 'w').write('kept across sleep')"
READ = "print(open('/opt/data/marker.txt').read())"


def pods(agent_id: str) -> list[str]:
    out = kubectl("-n", f"agent-{agent_id}", "get", "pods", "-l", "app=hermes", "-o", "name")
    return out.split()


def phase(agent_id: str) -> str:
    return kubectl("get", "agents.fleetdeck.dev", agent_id, "-o", "jsonpath={.status.phase}")


def test_sleep_removes_the_pod_and_wake_brings_back_its_state(
    api: Api,
    admin: dict[str, str],
    throwaway: str,
) -> None:
    first_wake = api.wake(throwaway, admin)
    assert len(pods(throwaway)) == 1 and phase(throwaway) == "Active"
    run_in_agent(throwaway, WRITE)

    sleep_seconds = api.sleep(throwaway, admin)
    assert pods(throwaway) == [] and phase(throwaway) == "Asleep"
    claim = kubectl(
        "-n", f"agent-{throwaway}", "get", "pvc", "state", "-o", "jsonpath={.status.phase}"
    )
    assert claim == "Bound"  # the volume outlives the pod

    wake_seconds = api.wake(throwaway, admin)
    assert run_in_agent(throwaway, READ) == "kept across sleep"
    assert api.wake(throwaway, admin) < 5  # already awake: no second cold start

    agents = api.http.get("/api/agents", headers=admin).json()
    mine = next(a for a in agents if a["id"] == throwaway)
    assert mine["state"] == "active" and mine["wake_count"] >= 2
    print(f"\nwake {first_wake:.1f}s  sleep {sleep_seconds:.1f}s  wake again {wake_seconds:.1f}s")


def test_suspend_stops_the_pod_and_refuses_wake(
    api: Api, admin: dict[str, str], throwaway: str
) -> None:
    api.wake(throwaway, admin)
    assert api.http.post(f"/api/agents/{throwaway}/suspend", headers=admin).status_code == 200
    kubectl(
        "-n",
        f"agent-{throwaway}",
        "wait",
        "--for=delete",
        "pod",
        "-l",
        "app=hermes",
        "--timeout=120s",
    )
    assert phase(throwaway) == "Suspended"
    assert api.http.post(f"/api/agents/{throwaway}/wake", headers=admin).status_code == 409
    assert api.http.post(f"/api/agents/{throwaway}/resume", headers=admin).status_code == 200


def test_deleting_an_agent_removes_its_namespace_and_keys(api: Api, admin: dict[str, str]) -> None:
    agent_id = "it-delete-me"
    api.http.delete(f"/api/agents/{agent_id}", headers=admin)
    created = api.http.post(
        "/api/agents", json={"agent_id": agent_id, "bundle": "biller"}, headers=admin
    )
    assert created.status_code == 201, created.text
    kubectl(
        "wait",
        "--for=jsonpath={.status.phase}=Asleep",
        f"agents.fleetdeck.dev/{agent_id}",
        "--timeout=120s",
    )
    assert kubectl("-n", "fleet-keys", "get", "secret", f"agent-keys-{agent_id}", "-o", "name")

    assert api.http.delete(f"/api/agents/{agent_id}", headers=admin).status_code == 204
    kubectl("wait", "--for=delete", f"namespace/agent-{agent_id}", "--timeout=180s")
    missing = kubectl("-n", "fleet-keys", "get", "secret", f"agent-keys-{agent_id}", check=False)
    assert missing == ""
