"""The model gateway: the only path from an agent to a model provider.

An agent presents its virtual key. The gateway asks the control plane who that is and what the
agent is working on, picks the route from the session's data class, forwards the call with the
real provider key, and reports usage and cost for attribution, audit and tracing. Agents never
see the provider key and cannot choose the model.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fleetdeck_common.control_client import ControlPlaneClient
from fleetdeck_common.errors import AuthenticationError, UpstreamError
from fleetdeck_common.identity import Identity, RunContext
from fleetdeck_common.logs import configure_logging
from fleetdeck_common.tokens import bearer_from_header
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fleetdeck_gateway import metrics
from fleetdeck_gateway.routing import Route, choose_route
from fleetdeck_gateway.settings import GatewaySettings, get_settings
from fleetdeck_gateway.tracing import CallRecord, NoopTracer, Tracer
from fleetdeck_gateway.usage import StreamUsageCollector, Usage, usage_from_body

log = logging.getLogger(__name__)


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def create_app(
    settings: GatewaySettings | None = None,
    *,
    control: ControlPlaneClient | None = None,
    upstream: httpx.AsyncClient | None = None,
    tracer: Tracer | None = None,
) -> FastAPI:
    """Build the gateway. Tests pass their own ``control`` and ``upstream`` clients."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    control = control or ControlPlaneClient(settings)
    upstream = upstream or httpx.AsyncClient(
        timeout=httpx.Timeout(settings.upstream_timeout_seconds, connect=10.0)
    )
    if tracer is None:
        tracer = NoopTracer()
        if settings.langfuse_host and settings.langfuse_public_key:
            from fleetdeck_gateway.langfuse_tracer import LangfuseTracer

            tracer = LangfuseTracer(settings)
    reports: set[asyncio.Task[None]] = set()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await asyncio.gather(*reports, return_exceptions=True)
        tracer.shutdown()
        await upstream.aclose()
        await control.aclose()

    app = FastAPI(title="Fleetdeck model gateway", version="0.1.0", lifespan=lifespan)

    async def report(
        identity: Identity,
        context: RunContext,
        route: Route,
        request_body: dict[str, Any],
        usage: Usage,
        status: int,
        started: float,
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        labels = {
            "person": identity.person_id or identity.agent_id,
            "department": identity.department or "none",
            "model": route.model,
            "route": route.name,
            "data_class": context.data_class,
        }
        metrics.CALLS.labels(**labels, status=str(status)).inc()
        metrics.TOKENS.labels(**labels, direction="input").inc(usage.input_tokens)
        metrics.TOKENS.labels(**labels, direction="output").inc(usage.output_tokens)
        metrics.COST.labels(**labels).inc(usage.cost_usd)
        metrics.LATENCY.labels(route=route.name).observe(latency_ms / 1000)
        tracer.record(CallRecord(identity, context, route, request_body, usage, status, latency_ms))
        try:
            await control.record_model_call(
                agent_id=identity.agent_id,
                run_id=context.run_id,
                route=route.name,
                data_class=context.data_class,
                model=route.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                status=status,
            )
        except UpstreamError:
            log.exception("could not report a model call of %s", identity.agent_id)

    def report_later(*args: Any) -> None:
        task = asyncio.create_task(report(*args))
        reports.add(task)
        task.add_done_callback(reports.discard)

    async def caller(request: Request) -> tuple[Identity, RunContext] | JSONResponse:
        token = bearer_from_header(request.headers.get("authorization"))
        if token is None:
            metrics.REFUSED.labels(reason="no_key").inc()
            return _error(401, "missing virtual key", "authentication_error")
        try:
            return await control.resolve(token, "model")
        except AuthenticationError:
            metrics.REFUSED.labels(reason="unknown_key").inc()
            return _error(401, "unknown or suspended virtual key", "authentication_error")
        except UpstreamError:
            # Fail closed: without the control plane there is no attribution and no routing.
            metrics.REFUSED.labels(reason="control_plane_down").inc()
            return _error(503, "identity service unavailable", "service_unavailable")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        """List the single alias agents may use."""
        who = await caller(request)
        if isinstance(who, JSONResponse):
            return who
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": settings.model_alias, "object": "model", "owned_by": "fleetdeck"}],
            }
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        """Forward a chat completion on the route the session's data class requires."""
        who = await caller(request)
        if isinstance(who, JSONResponse):
            return who
        identity, context = who
        try:
            body = json.loads(await request.body())
        except ValueError:
            return _error(400, "body must be JSON", "invalid_request_error")
        if not isinstance(body, dict):
            return _error(400, "body must be a JSON object", "invalid_request_error")

        route = choose_route(context.data_class, settings)
        stream = bool(body.get("stream"))
        body["model"] = route.model
        body["usage"] = {"include": True}
        if stream:
            body["stream_options"] = {"include_usage": True}
        body.pop("user", None)  # the provider learns nothing about who is asking
        outgoing = upstream.build_request(
            "POST",
            f"{route.base_url}/chat/completions",
            json=body,
            headers={
                "Authorization": f"Bearer {route.api_key}",
                "Accept-Encoding": "identity",
                "X-Title": "Fleetdeck",
            },
        )
        started = time.monotonic()
        try:
            response = await upstream.send(outgoing, stream=True)
        except httpx.HTTPError as exc:
            log.warning("provider unreachable for %s: %s", identity.agent_id, exc)
            report_later(identity, context, route, body, Usage(), 502, started)
            return _error(502, "model provider unreachable", "upstream_error")

        content_type = response.headers.get("content-type", "application/json")
        if not stream or response.status_code >= 400:
            payload = await response.aread()
            await response.aclose()
            report_later(
                identity,
                context,
                route,
                body,
                usage_from_body(payload),
                response.status_code,
                started,
            )
            return Response(payload, status_code=response.status_code, media_type=content_type)

        collector = StreamUsageCollector()

        async def passthrough() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    collector.feed(chunk)
                    yield chunk
            finally:
                await response.aclose()
                report_later(
                    identity,
                    context,
                    route,
                    body,
                    collector.finish(),
                    response.status_code,
                    started,
                )

        return StreamingResponse(
            passthrough(), status_code=response.status_code, media_type=content_type
        )

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
    async def not_served(rest: str) -> JSONResponse:
        return _error(404, f"/v1/{rest} is not served by this gateway", "not_found")

    return app
