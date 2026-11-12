import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fleetdeck_common.control_client import ControlPlaneClient
from fleetdeck_gateway.app import create_app
from fleetdeck_gateway.settings import GatewaySettings
from fleetdeck_gateway.tracing import CallRecord

REAL_KEY = "sk-or-real-provider-key"
SETTINGS = GatewaySettings(
    OPENROUTER_API_KEY=REAL_KEY,
    internal_token="internal",
    control_plane_url="http://cp",
    default_model="deepseek/default",
    restricted_model="inhouse/restricted",
)
IDENTITY = {
    "agent_id": "elena-park",
    "person_id": "elena-park",
    "person_name": "Dr. Elena Park",
    "role": "physician",
    "department": "Clinical",
}
CHAT = {"model": "fleet-default", "messages": [{"role": "user", "content": "hi"}]}
AGENT = {"Authorization": "Bearer fdm_agent_virtual_key"}
STREAM = (
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    b'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":5,"cost":0.00019,'
    b'"prompt_tokens_details":{"cached_tokens":1000}}}\n\n'
    b"data: [DONE]\n\n"
)


class RecordingTracer:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def record(self, call: CallRecord) -> None:
        self.calls.append(call)

    def shutdown(self) -> None:
        pass


class Harness:
    def __init__(
        self, router: respx.MockRouter, client: httpx.AsyncClient, tracer: RecordingTracer
    ) -> None:
        self.router, self.client, self.tracer = router, client, tracer
        self.model_calls = router.post("http://cp/internal/model-calls").respond(204)
        self.provider = router.post("https://openrouter.ai/api/v1/chat/completions")

    def identity(self, data_class: str = "general", status: int = 200) -> respx.Route:
        context = {
            "run_id": "run_1",
            "session_id": "ses_1",
            "case_id": "case-1",
            "data_class": data_class,
            "trigger": "chat",
            "trace_id": "a" * 32,
        }
        body: dict[str, Any] = {"identity": IDENTITY, "context": context}
        return self.router.post("http://cp/internal/identity").respond(
            status, json=body if status == 200 else {"detail": "no"}
        )

    def reported(self) -> dict[str, Any]:
        return json.loads(self.model_calls.calls.last.request.content)  # type: ignore[no-any-return]

    def sent_to_provider(self) -> httpx.Request:
        return self.provider.calls.last.request  # type: ignore[no-any-return]


@pytest.fixture
async def gateway() -> AsyncIterator[Harness]:
    with respx.mock(assert_all_called=False) as router:
        tracer = RecordingTracer()
        app = create_app(
            SETTINGS,
            control=ControlPlaneClient(SETTINGS),
            upstream=httpx.AsyncClient(),
            tracer=tracer,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
                yield Harness(router, client, tracer)


async def test_unknown_or_missing_key_never_reaches_the_provider(gateway: Harness) -> None:
    gateway.identity(status=401)
    for headers in ({}, {"Authorization": "Bearer stolen"}):
        response = await gateway.client.post("/v1/chat/completions", json=CHAT, headers=headers)
        assert response.status_code == 401
    assert gateway.provider.call_count == 0 and gateway.model_calls.call_count == 0


async def test_fails_closed_when_the_control_plane_is_down(gateway: Harness) -> None:
    gateway.router.post("http://cp/internal/identity").mock(side_effect=httpx.ConnectError("x"))
    response = await gateway.client.post("/v1/chat/completions", json=CHAT, headers=AGENT)
    assert response.status_code == 503 and gateway.provider.call_count == 0


async def test_call_uses_the_real_key_and_the_default_route_and_hides_both(
    gateway: Harness,
) -> None:
    gateway.identity()
    gateway.provider.respond(
        200,
        json={
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 3, "cost": 0.00014},
        },
    )
    body = {**CHAT, "model": "openai/gpt-something-expensive", "user": "elena.park@example"}
    response = await gateway.client.post("/v1/chat/completions", json=body, headers=AGENT)

    assert response.status_code == 200
    assert REAL_KEY not in response.text and REAL_KEY not in str(response.headers)
    sent = gateway.sent_to_provider()
    assert sent.headers["authorization"] == f"Bearer {REAL_KEY}"
    assert sent.headers["accept-encoding"] == "identity"
    payload = json.loads(sent.content)
    assert payload["model"] == "deepseek/default"  # the agent cannot pick the model
    assert "user" not in payload and "fdm_agent_virtual_key" not in sent.content.decode()

    await _settle(gateway)
    reported = gateway.reported()
    assert reported["agent_id"] == "elena-park" and reported["run_id"] == "run_1"
    assert (reported["route"], reported["model"]) == ("default", "deepseek/default")
    assert (reported["input_tokens"], reported["output_tokens"]) == (900, 3)
    assert reported["cost_usd"] == pytest.approx(0.00014)
    traced = gateway.tracer.calls[0]
    assert traced.context.trace_id == "a" * 32 and traced.usage.output_text == "Hello"


async def test_restricted_session_goes_to_the_restricted_route(gateway: Harness) -> None:
    gateway.identity(data_class="restricted")
    gateway.provider.respond(200, json={"choices": [{"message": {"content": "ok"}}]})
    await gateway.client.post("/v1/chat/completions", json=CHAT, headers=AGENT)
    assert json.loads(gateway.sent_to_provider().content)["model"] == "inhouse/restricted"
    await _settle(gateway)
    assert gateway.reported()["route"] == "restricted"
    assert gateway.reported()["data_class"] == "restricted"


async def test_restricted_route_can_point_at_an_in_house_endpoint() -> None:
    settings = SETTINGS.model_copy(
        update={
            "restricted_upstream_url": "http://llm.internal/v1",
            "restricted_api_key": "inhouse-key",
        }
    )
    with respx.mock(assert_all_called=False) as router:
        harness_tracer = RecordingTracer()
        app = create_app(
            settings,
            control=ControlPlaneClient(settings),
            upstream=httpx.AsyncClient(),
            tracer=harness_tracer,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gw"
            ) as client,
        ):
            harness = Harness(router, client, harness_tracer)
            harness.identity(data_class="restricted")
            inhouse = router.post("http://llm.internal/v1/chat/completions").respond(
                200, json={"choices": []}
            )
            await client.post("/v1/chat/completions", json=CHAT, headers=AGENT)
            assert inhouse.call_count == 1 and harness.provider.call_count == 0
            assert inhouse.calls.last.request.headers["authorization"] == "Bearer inhouse-key"


async def test_stream_passes_through_unchanged_and_usage_is_collected(gateway: Harness) -> None:
    gateway.identity()
    gateway.provider.respond(200, content=STREAM, headers={"content-type": "text/event-stream"})
    response = await gateway.client.post(
        "/v1/chat/completions", json={**CHAT, "stream": True}, headers=AGENT
    )
    assert response.content == STREAM
    assert response.headers["content-type"].startswith("text/event-stream")
    assert json.loads(gateway.sent_to_provider().content)["stream_options"] == {
        "include_usage": True
    }
    await _settle(gateway)
    reported = gateway.reported()
    assert (reported["input_tokens"], reported["output_tokens"], reported["cached_tokens"]) == (
        1200,
        5,
        1000,
    )
    assert reported["cost_usd"] == pytest.approx(0.00019)
    assert gateway.tracer.calls[0].usage.output_text == "Hello"


async def test_provider_errors_are_passed_on_and_recorded(gateway: Harness) -> None:
    gateway.identity()
    gateway.provider.respond(429, json={"error": {"message": "rate limited"}})
    response = await gateway.client.post("/v1/chat/completions", json=CHAT, headers=AGENT)
    assert response.status_code == 429
    await _settle(gateway)
    assert gateway.reported()["status"] == 429


async def test_only_the_alias_is_listed_and_other_paths_are_not_served(gateway: Harness) -> None:
    gateway.identity()
    models = await gateway.client.get("/v1/models", headers=AGENT)
    assert [m["id"] for m in models.json()["data"]] == ["fleet-default"]
    assert (await gateway.client.get("/v1/models")).status_code == 401
    assert (await gateway.client.post("/v1/embeddings", json={}, headers=AGENT)).status_code == 404


async def _settle(gateway: Harness) -> None:
    """Let the background report of the last call finish."""
    import asyncio

    for _ in range(50):
        if gateway.model_calls.call_count:
            return
        await asyncio.sleep(0.01)
