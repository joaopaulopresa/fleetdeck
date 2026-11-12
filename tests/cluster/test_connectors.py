"""Connector calls made from inside real agent pods, with the pod's own token."""

import json
import time

import pytest

from .conftest import Api, run_in_agent

pytestmark = pytest.mark.cluster

MCP_CALL = """
import json, os, sys, urllib.error, urllib.request
system, tool, arguments, token = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4]
token = os.environ["FLEET_CONNECTOR_TOKEN"] if token == "own" else token
body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments}}
request = urllib.request.Request(
    f"http://connector-{system}.fleet-system.svc.cluster.local:8000/mcp",
    data=json.dumps(body).encode(), method="POST",
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"})
try:
    reply = json.load(urllib.request.urlopen(request, timeout=20))
    print(json.dumps({"http": 200, "text": reply["result"]["content"][0]["text"]}))
except urllib.error.HTTPError as error:
    print(json.dumps({"http": error.code, "text": ""}))
"""


def call(
    agent_id: str, system: str, tool: str, arguments: dict[str, str], token: str = "own"
) -> tuple[int, str]:
    out = json.loads(run_in_agent(agent_id, MCP_CALL, system, tool, json.dumps(arguments), token))
    return out["http"], out["text"]


@pytest.fixture(scope="module")
def awake(api: Api, admin: dict[str, str]) -> None:
    for agent_id in ("elena-park", "priya-nair", "marcus-reyes"):
        api.wake(agent_id, admin)


@pytest.mark.usefixtures("awake")
def test_the_lawyers_agent_is_refused_the_patient_record_and_it_is_audited(
    api: Api, admin: dict[str, str]
) -> None:
    status, text = call(
        "priya-nair", "patient-record", "get_patient_summary", {"patient_id": "PAT-0001"}
    )
    assert status == 200 and "access denied" in text
    assert "Whitaker" not in text and "cardiomyopathy" not in text
    time.sleep(1)
    events = api.http.get(
        "/api/audit",
        headers=admin,
        params={"agent": "priya-nair", "system": "patient-record", "outcome": "denied"},
    ).json()
    assert events and events[0]["action"] == "patient-record.get_patient_summary"
    assert events[0]["person_id"] == "priya-nair"


@pytest.mark.usefixtures("awake")
def test_the_treating_physicians_agent_reads_the_chart() -> None:
    status, text = call(
        "elena-park", "patient-record", "get_patient_summary", {"patient_id": "PAT-0001"}
    )
    assert status == 200 and "Harold Whitaker" in text and "Ischemic cardiomyopathy" in text


@pytest.mark.usefixtures("awake")
def test_a_token_that_is_not_an_agents_is_rejected_by_the_connector_itself() -> None:
    assert (
        call(
            "priya-nair",
            "patient-record",
            "get_patient_summary",
            {"patient_id": "PAT-0001"},
            token="fdc_forged",
        )[0]
        == 401
    )
    assert call("priya-nair", "billing", "list_denials", {}, token="fdc_forged")[0] == 401


@pytest.mark.usefixtures("awake")
def test_knowledge_is_filtered_by_row_level_security_in_the_cluster() -> None:
    _, lawyer = call("priya-nair", "knowledge", "search_knowledge", {"query": "notice dispute"})
    _, biller = call("marcus-reyes", "knowledge", "search_knowledge", {"query": "notice dispute"})
    assert "kb-notice-of-dispute-template" in lawyer
    assert "kb-notice-of-dispute-template" not in biller
    _, hidden = call(
        "marcus-reyes",
        "knowledge",
        "get_document",
        {"document_id": "kb-notice-of-dispute-template"},
    )
    assert "not found" in hidden


@pytest.mark.live
def test_a_run_that_reads_the_chart_is_routed_to_the_restricted_model(
    api: Api, admin: dict[str, str]
) -> None:
    elena = api.sign_in("elena-park")
    sent = api.http.post(
        "/api/me/chat",
        headers=elena,
        json={
            "text": "Open Harold Whitaker's chart (patient PAT-0001) and tell me his most recent "
            "LVEF in one short sentence."
        },
    ).json()
    deadline = time.monotonic() + 240
    run: dict[str, object] = {}
    while time.monotonic() < deadline:
        run = api.http.get(f"/api/runs/{sent['run_id']}", headers=elena).json()
        if run["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    assert run["status"] == "completed", run
    assert "28" in str(run["output"])
    time.sleep(1)
    calls = api.http.get(
        "/api/audit", headers=admin, params={"kind": "model_call", "run": sent["run_id"]}
    ).json()
    routes = [c["detail"]["route"] for c in reversed(calls)]
    # Before the chart is read the session is general; once it is, every call is restricted.
    assert routes[0] == "default" and "restricted" in routes
    assert routes[routes.index("restricted") :] == ["restricted"] * (
        len(routes) - routes.index("restricted")
    )
    tools = api.http.get(
        "/api/audit", headers=admin, params={"kind": "tool_call", "run": sent["run_id"]}
    ).json()
    assert any(
        t["action"].startswith("patient-record.") and t["outcome"] == "allowed" for t in tools
    )
