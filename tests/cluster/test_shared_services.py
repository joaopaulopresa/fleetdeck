"""The model gateway and the egress proxy as seen from inside a real agent pod."""

import json
import time

import pytest

from .conftest import Api, kubectl, run_in_agent

pytestmark = pytest.mark.cluster

GATEWAY = "http://model-gateway.fleet-system.svc.cluster.local:4000"

GATEWAY_PROBE = f"""
import json, os, urllib.error, urllib.request
def status(key):
    request = urllib.request.Request("{GATEWAY}/v1/models",
                                     headers={{"Authorization": "Bearer " + key}})
    try:
        return urllib.request.urlopen(request, timeout=10).status
    except urllib.error.HTTPError as error:
        return error.code
print(json.dumps({{
    "own_key": status(os.environ["FLEET_MODEL_KEY"]),
    "wrong_key": status("fdm_not-a-real-key"),
    "connector_token_as_model_key": status(os.environ["FLEET_CONNECTOR_TOKEN"]),
    "provider_key_in_env": any("OPENROUTER" in name or value.startswith("sk-or-")
                               for name, value in os.environ.items()),
}}))
"""

EGRESS_PROBE = """
import json, os, socket, sys
def connect(destination, token):
    sock = socket.create_connection(("egress-proxy.fleet-system.svc.cluster.local", 3128), 10)
    auth = f"Proxy-Authorization: Bearer {token}\\r\\n" if token else ""
    sock.sendall(f"CONNECT {destination} HTTP/1.1\\r\\n{auth}\\r\\n".encode())
    line = sock.recv(200).split(b"\\r\\n")[0].decode()
    sock.close()
    return int(line.split()[1])
token = os.environ["FLEET_CONNECTOR_TOKEN"]
print(json.dumps({
    "allowed": connect("www.cms.gov:443", token),
    "not_listed": connect("example.org:443", token),
    "wrong_port": connect("www.cms.gov:22", token),
    "no_token": connect("www.cms.gov:443", None),
    "wrong_token": connect("www.cms.gov:443", "fdc_stolen"),
}))
"""


@pytest.fixture(scope="module")
def awake(api: Api, admin: dict[str, str], throwaway: str) -> str:
    api.wake(throwaway, admin)
    return throwaway


def test_gateway_accepts_only_the_agents_own_virtual_key(awake: str) -> None:
    result = json.loads(run_in_agent(awake, GATEWAY_PROBE))
    assert result == {
        "own_key": 200,
        "wrong_key": 401,
        "connector_token_as_model_key": 401,
        "provider_key_in_env": False,
    }


def test_provider_key_is_not_in_the_agents_namespace_or_volume(awake: str) -> None:
    secret = kubectl(
        "-n", f"agent-{awake}", "get", "secret", "agent-keys", "-o", "jsonpath={.data}"
    )
    assert set(json.loads(secret)) == {"FLEET_MODEL_KEY", "FLEET_CONNECTOR_TOKEN", "API_SERVER_KEY"}
    found = run_in_agent(
        awake,
        """
import pathlib
hits = [str(p) for p in pathlib.Path('/opt/data').rglob('*')
        if p.is_file() and p.stat().st_size < 2_000_000 and b'sk-or-' in p.read_bytes()]
print(hits)
""",
    )
    assert found == "[]"


def test_suspended_agents_key_stops_working(api: Api, admin: dict[str, str]) -> None:
    agent_id = "it-suspend-key"
    api.http.delete(f"/api/agents/{agent_id}", headers=admin)
    assert (
        api.http.post(
            "/api/agents", json={"agent_id": agent_id, "bundle": "biller"}, headers=admin
        ).status_code
        == 201
    )
    try:
        kubectl(
            "wait",
            "--for=jsonpath={.status.phase}=Asleep",
            f"agents.fleetdeck.dev/{agent_id}",
            "--timeout=120s",
        )
        key = kubectl(
            "-n",
            f"agent-{agent_id}",
            "get",
            "secret",
            "agent-keys",
            "-o",
            "jsonpath={.data.FLEET_MODEL_KEY}",
        )
        check = (
            "import base64, sys, urllib.request, urllib.error\n"
            f"key = base64.b64decode('{key}').decode()\n"
            f"request = urllib.request.Request('{GATEWAY}/v1/models', "
            "headers={'Authorization': 'Bearer ' + key})\n"
            "try:\n    print(urllib.request.urlopen(request, timeout=10).status)\n"
            "except urllib.error.HTTPError as error:\n    print(error.code)\n"
        )
        # Asked from another agent's pod: the key works while its owner is in good standing...
        other = api.http.get("/api/agents", headers=admin).json()
        probe_from = next(a["id"] for a in other if a["state"] == "active")
        assert run_in_agent(probe_from, check) == "200"
        # ...and is refused the moment the owner is suspended.
        assert api.http.post(f"/api/agents/{agent_id}/suspend", headers=admin).status_code == 200
        assert run_in_agent(probe_from, check) == "401"
    finally:
        api.http.delete(f"/api/agents/{agent_id}", headers=admin)


def test_egress_goes_only_to_the_allow_list_and_is_audited(
    api: Api, admin: dict[str, str], awake: str
) -> None:
    result = json.loads(run_in_agent(awake, EGRESS_PROBE))
    assert result == {
        "allowed": 200,
        "not_listed": 403,
        "wrong_port": 403,
        "no_token": 407,
        "wrong_token": 407,
    }
    time.sleep(1)
    events = api.http.get(
        "/api/audit", headers=admin, params={"kind": "egress", "agent": awake}
    ).json()
    outcomes = {(event["action"], event["outcome"]) for event in events}
    assert ("www.cms.gov:443", "allowed") in outcomes
    assert ("example.org:443", "denied") in outcomes
    assert ("www.cms.gov:22", "denied") in outcomes


@pytest.mark.live
def test_a_real_model_call_is_attributed_to_the_person_and_audited(
    api: Api, admin: dict[str, str]
) -> None:
    elena = api.sign_in("elena-park")
    sent = api.http.post(
        "/api/me/chat", headers=elena, json={"text": "Reply with the single word: ready"}
    ).json()
    deadline = time.monotonic() + 180
    run: dict[str, object] = {}
    while time.monotonic() < deadline:
        run = api.http.get(f"/api/runs/{sent['run_id']}", headers=elena).json()
        if run["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    assert run["status"] == "completed", run
    assert "ready" in str(run["output"]).lower()
    time.sleep(1)
    calls = api.http.get(
        "/api/audit", headers=admin, params={"kind": "model_call", "run": sent["run_id"]}
    ).json()
    assert calls, "the gateway recorded no model call for this run"
    call = calls[-1]
    assert call["person_id"] == "elena-park" and call["system"] == "model-gateway"
    assert call["detail"]["route"] == "default" and call["detail"]["input_tokens"] > 0
    assert call["trace_id"]
