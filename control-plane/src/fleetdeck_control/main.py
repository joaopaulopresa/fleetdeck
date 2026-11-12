"""The control-plane ASGI application."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fleetdeck_common import errors
from fleetdeck_common.logs import configure_logging
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fleetdeck_control.api import agents, auth, chat, internal
from fleetdeck_control.context import AppContext, build_context
from fleetdeck_control.settings import Settings, get_settings

log = logging.getLogger(__name__)

_STATUS: dict[type[errors.FleetdeckError], int] = {
    errors.NotFoundError: 404,
    errors.ConflictError: 409,
    errors.AuthenticationError: 401,
    errors.PolicyDeniedError: 403,
    errors.PermissionDeniedError: 403,
    errors.AgentUnavailableError: 503,
    errors.UpstreamError: 502,
}


def create_app(settings: Settings | None = None, context: AppContext | None = None) -> FastAPI:
    """Build the application. Tests pass their own ``context`` with fakes."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = context or build_context(settings)
        app.state.context = ctx
        tasks: list[asyncio.Task[None]] = []
        if settings.background_tasks:
            tasks.append(asyncio.create_task(ctx.reaper.run_forever(), name="idle-reaper"))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.runs.shutdown()
            if context is None:
                await ctx.db.dispose()

    app = FastAPI(title="Fleetdeck control plane", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(errors.FleetdeckError)
    async def fleetdeck_error(_: Request, exc: errors.FleetdeckError) -> JSONResponse:
        for kind, code in _STATUS.items():
            if isinstance(exc, kind):
                return JSONResponse({"detail": str(exc), "error": kind.__name__}, status_code=code)
        log.exception("unhandled fleetdeck error")
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "error": "ValueError"}, status_code=422)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(chat.router)
    app.include_router(internal.router)
    return app
