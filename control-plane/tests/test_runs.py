import json

import httpx
from fleetdeck_control.agents.service import NewAgent
from fleetdeck_control.context import AppContext
from fleetdeck_control.hermes import RunEvent
from fleetdeck_control.models import Agent, AuditEvent, ModelCall
from sqlalchemy import select

from .conftest import FakeHermes, sign_in

INTERNAL = {"Authorization": "Bearer internal-test-token"}


async def _provision(context: AppContext, agent_id: str = "elena-park") -> None:
    await context.agents.create(NewAgent(agent_id, "physician", person_id=agent_id))


async def test_chat_wakes_the_agent_runs_and_keeps_the_transcript(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context)
    assert isinstance(context.hermes, FakeHermes)
    context.hermes.script = [
        RunEvent("message.delta", {"event": "message.delta", "delta": "Hel"}),
        RunEvent("tool.started", {"event": "tool.started", "tool": "memory"}),
        RunEvent("tool.completed", {"event": "tool.completed", "tool": "memory", "error": False}),
        RunEvent(
            "run.completed",
            {
                "event": "run.completed",
                "output": "Hello Elena",
                "usage": {"input_tokens": 120, "output_tokens": 4},
            },
        ),
    ]
    elena = await sign_in(client, "elena-park")
    sent = await client.post("/api/me/chat", json={"text": "hi"}, headers=elena)
    assert sent.status_code == 200, sent.text
    run_id, session_id = sent.json()["run_id"], sent.json()["session_id"]

    run = await context.runs.wait(run_id)
    assert (run.status, run.output, run.input_tokens) == ("completed", "Hello Elena", 120)
    assert run.wake_seconds is not None  # the run records how long the wake took
    sent_to_hermes = context.hermes.runs[0]
    assert sent_to_hermes["session_id"] == session_id and sent_to_hermes["text"] == "hi"
    assert sent_to_hermes["runs_key"].startswith("fdr_")

    messages = (await client.get(f"/api/me/sessions/{session_id}/messages", headers=elena)).json()
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "hi"),
        ("tool", "memory"),
        ("assistant", "Hello Elena"),
    ]

    events = (await client.get(f"/api/runs/{run_id}/events", headers=elena)).text
    final = json.loads(events.strip().removeprefix("data: "))
    assert final["type"] == "completed" and final["output"] == "Hello Elena"

    async with context.db.session() as session:
        agent = await session.get(Agent, "elena-park")
        tool_audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.run_id == run_id, AuditEvent.kind == "tool_call")
        )
    assert agent is not None and agent.current_run_id is None
    assert (agent.state, agent.wake_count) == ("active", 1)
    assert tool_audit is not None and tool_audit.action == "memory"


async def test_a_second_message_continues_the_same_session(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context)
    elena = await sign_in(client, "elena-park")
    first = (await client.post("/api/me/chat", json={"text": "one"}, headers=elena)).json()
    await context.runs.wait(first["run_id"])
    second = (
        await client.post(
            "/api/me/chat", json={"text": "two", "session_id": first["session_id"]}, headers=elena
        )
    ).json()
    await context.runs.wait(second["run_id"])
    assert second["session_id"] == first["session_id"]
    sessions = (await client.get("/api/me/sessions", headers=elena)).json()
    assert [s["id"] for s in sessions] == [first["session_id"]]


async def test_people_cannot_read_or_continue_each_others_conversations(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context, "elena-park")
    await _provision(context, "marcus-reyes")
    elena = await sign_in(client, "elena-park")
    marcus = await sign_in(client, "marcus-reyes")
    sent = (await client.post("/api/me/chat", json={"text": "private"}, headers=elena)).json()
    await context.runs.wait(sent["run_id"])

    session_url = f"/api/me/sessions/{sent['session_id']}/messages"
    assert (await client.get(session_url, headers=marcus)).status_code == 404
    assert (await client.get(f"/api/runs/{sent['run_id']}", headers=marcus)).status_code == 403
    hijack = await client.post(
        "/api/me/chat", json={"text": "x", "session_id": sent["session_id"]}, headers=marcus
    )
    assert hijack.status_code == 404


async def test_run_fails_cleanly_when_the_agent_cannot_wake(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context)
    assert isinstance(context.hermes, FakeHermes)
    context.hermes.never_ready.add("elena-park")
    elena = await sign_in(client, "elena-park")
    sent = (await client.post("/api/me/chat", json={"text": "hi"}, headers=elena)).json()
    run = await context.runs.wait(sent["run_id"])
    assert run.status == "failed" and "did not answer" in (run.error or "")
    assert not context.activator.is_busy("elena-park")


async def test_internal_api_needs_the_internal_token(client: httpx.AsyncClient) -> None:
    body = {"token": "x", "kind": "model"}
    assert (await client.post("/internal/identity", json=body)).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert (await client.post("/internal/identity", json=body, headers=wrong)).status_code == 401
    assert (await client.post("/internal/identity", json=body, headers=INTERNAL)).status_code == 401


async def test_tokens_resolve_to_their_agent_only_for_the_right_purpose(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context)
    keys = await context.cluster.read_keys("elena-park")

    resolved = await client.post(
        "/internal/identity", json={"token": keys.model_key, "kind": "model"}, headers=INTERNAL
    )
    assert resolved.status_code == 200
    identity = resolved.json()["identity"]
    assert (identity["agent_id"], identity["role"], identity["department"]) == (
        "elena-park",
        "physician",
        "Clinical",
    )
    assert resolved.json()["context"]["run_id"] is None

    # A model key is not a connector token, and the runs key is neither.
    for token, kind in (
        (keys.model_key, "connector"),
        (keys.connector_token, "model"),
        (keys.runs_key, "model"),
    ):
        refused = await client.post(
            "/internal/identity", json={"token": token, "kind": kind}, headers=INTERNAL
        )
        assert refused.status_code == 401

    await context.agents.set_suspended("elena-park", True, actor=None)
    suspended = await client.post(
        "/internal/identity", json={"token": keys.model_key, "kind": "model"}, headers=INTERNAL
    )
    assert suspended.status_code == 401


async def test_model_calls_are_attributed_and_audited_and_restricted_data_sticks(
    client: httpx.AsyncClient, context: AppContext
) -> None:
    await _provision(context)
    keys = await context.cluster.read_keys("elena-park")
    session_row = await context.runs.open_session("elena-park", kind="chat", case_id="case-1")
    assert isinstance(context.hermes, FakeHermes)
    seen: list[dict[str, str]] = []

    async def events(agent_id: str, runs_key: str, run_id: str):  # type: ignore[no-untyped-def]
        # While the run is in flight, a connector reports restricted data and the gateway asks
        # what the agent is working on.
        raised = await client.post(
            "/internal/sessions/raise-data-class",
            headers=INTERNAL,
            json={"agent_id": "elena-park", "data_class": "restricted", "system": "patient-record"},
        )
        assert raised.status_code == 204
        resolved = await client.post(
            "/internal/identity", json={"token": keys.model_key, "kind": "model"}, headers=INTERNAL
        )
        seen.append(resolved.json()["context"])
        yield RunEvent("run.completed", {"event": "run.completed", "output": "done"})

    context.hermes.events = events  # type: ignore[method-assign]
    run = await context.runs.submit(
        "elena-park", "summarise", trigger="chat", session_id=session_row.id
    )
    await context.runs.wait(run.id)
    assert seen[0]["run_id"] == run.id
    assert (seen[0]["data_class"], seen[0]["case_id"]) == ("restricted", "case-1")

    # Lowering is not possible.
    await context.runs.submit("elena-park", "again", trigger="chat", session_id=session_row.id)
    reported = await client.post(
        "/internal/model-calls",
        headers=INTERNAL,
        json={
            "agent_id": "elena-park",
            "run_id": run.id,
            "route": "restricted",
            "data_class": "restricted",
            "model": "google/gemma",
            "input_tokens": 900,
            "output_tokens": 30,
            "cost_usd": 0.0001,
            "latency_ms": 800,
        },
    )
    assert reported.status_code == 204
    async with context.db.session() as session:
        call = await session.scalar(select(ModelCall).where(ModelCall.run_id == run.id))
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.run_id == run.id, AuditEvent.kind == "model_call")
        )
    assert call is not None and event is not None
    assert (call.person_id, call.department, call.case_id, call.route) == (
        "elena-park",
        "Clinical",
        "case-1",
        "restricted",
    )
    assert call.trace_id == run.trace_id == event.trace_id
    assert event.person_id == "elena-park" and event.system == "model-gateway"
