"""Client for the Hermes Runs API of one agent pod.

Verified against Hermes Agent v0.23.1: ``POST /v1/runs`` returns 202 with a ``run_id``;
``GET /v1/runs/{id}/events`` is a server-sent event stream of JSON objects with an ``event``
field; ``GET /health`` needs no key.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from fleetdeck_common.errors import UpstreamError
from fleetdeck_common.naming import hermes_url
from fleetdeck_common.settings import CommonSettings

TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.interrupted", "run.cancelled"})


@dataclass(frozen=True)
class RunEvent:
    """One event of a run's stream."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        """Whether this event ends the run."""
        return self.event in TERMINAL_EVENTS


class HermesClient(Protocol):
    """What the control plane needs from an agent's Hermes API."""

    async def healthy(self, agent_id: str) -> bool:
        """Return whether the agent's API answers its health check."""

    async def start_run(
        self,
        agent_id: str,
        runs_key: str,
        *,
        text: str,
        session_id: str,
        instructions: str | None = None,
    ) -> str:
        """Start a run and return its id."""

    def events(self, agent_id: str, runs_key: str, run_id: str) -> AsyncIterator[RunEvent]:
        """Yield the run's events until it ends."""


class HttpHermesClient:
    """:class:`HermesClient` over HTTP to the agent's in-cluster service."""

    def __init__(self, settings: CommonSettings, *, run_timeout: float = 600.0) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(run_timeout, connect=3.0))

    async def aclose(self) -> None:
        """Close the HTTP connection pool."""
        await self._client.aclose()

    async def healthy(self, agent_id: str) -> bool:
        """Return True when ``/health`` answers 200 within two seconds."""
        try:
            response = await self._client.get(
                f"{hermes_url(agent_id, self._settings)}/health", timeout=2.0
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def start_run(
        self,
        agent_id: str,
        runs_key: str,
        *,
        text: str,
        session_id: str,
        instructions: str | None = None,
    ) -> str:
        """Start a run in ``session_id`` and return the Hermes run id."""
        body: dict[str, Any] = {"input": text, "session_id": session_id}
        if instructions:
            body["instructions"] = instructions
        try:
            response = await self._client.post(
                f"{hermes_url(agent_id, self._settings)}/v1/runs",
                headers={"Authorization": f"Bearer {runs_key}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"hermes {agent_id}: {exc}") from exc
        if response.status_code not in (200, 202):
            raise UpstreamError(f"hermes {agent_id}: {response.status_code} {response.text[:200]}")
        return str(response.json()["run_id"])

    async def events(self, agent_id: str, runs_key: str, run_id: str) -> AsyncIterator[RunEvent]:
        """Stream the run's events, ending after the first terminal event."""
        url = f"{hermes_url(agent_id, self._settings)}/v1/runs/{run_id}/events"
        try:
            async with self._client.stream(
                "GET", url, headers={"Authorization": f"Bearer {runs_key}"}
            ) as response:
                if response.status_code != 200:
                    raise UpstreamError(f"hermes {agent_id}: events {response.status_code}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    event = RunEvent(event=str(data.get("event", "")), data=data)
                    yield event
                    if event.terminal:
                        return
        except httpx.HTTPError as exc:
            raise UpstreamError(f"hermes {agent_id}: {exc}") from exc
