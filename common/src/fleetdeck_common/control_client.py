"""Client for the control plane's internal API, used by the gateway, connectors and egress proxy."""

from typing import Any

import httpx

from fleetdeck_common.errors import AuthenticationError, UpstreamError
from fleetdeck_common.identity import Identity, RunContext
from fleetdeck_common.settings import CommonSettings


class ControlPlaneClient:
    """Typed access to ``/internal`` on the control plane."""

    def __init__(self, settings: CommonSettings, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._base = settings.control_plane_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.internal_token}"}

    async def aclose(self) -> None:
        """Close the HTTP connection pool."""
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(f"{self._base}{path}", json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"control plane unreachable: {exc}") from exc

    async def resolve(self, token: str, kind: str) -> tuple[Identity, RunContext]:
        """Resolve an agent's ``model`` key or ``connector`` token.

        Raises :class:`AuthenticationError` for unknown tokens and suspended agents.
        """
        response = await self._post("/internal/identity", {"token": token, "kind": kind})
        if response.status_code == 401:
            raise AuthenticationError("unknown or suspended agent")
        if response.status_code != 200:
            raise UpstreamError(f"control plane: {response.status_code}")
        body = response.json()
        return Identity.model_validate(body["identity"]), RunContext.model_validate(body["context"])

    async def record_model_call(self, **fields: Any) -> None:
        """Report a finished model call for attribution and audit."""
        await self._post("/internal/model-calls", fields)

    async def audit(self, **fields: Any) -> None:
        """Append an audit record."""
        await self._post("/internal/audit", fields)

    async def raise_data_class(self, agent_id: str, data_class: str, system: str) -> None:
        """Tell the platform that data of this class entered the agent's current session."""
        await self._post(
            "/internal/sessions/raise-data-class",
            {"agent_id": agent_id, "data_class": data_class, "system": system},
        )
