"""What every connector shares: who is calling, the permission check, and the systems client."""

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from fleetdeck_common.errors import (
    AuthenticationError,
    FleetdeckError,
    NotFoundError,
    UpstreamError,
)
from fleetdeck_common.identity import Decision
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

log = logging.getLogger(__name__)

Result = dict[str, Any]
Tool = Callable[..., Awaitable[Result]]


class AccessDeniedError(FleetdeckError):
    """The control plane refused the call. The reason is safe to show to the agent."""


class Control(Protocol):
    """The part of the control-plane client that connectors use."""

    async def authorize(
        self, token: str, system: str, tool: str, *, case_id: str | None = None, record: str = ""
    ) -> Decision:
        """Decide and audit one call."""

    async def member_cases(self, token: str) -> list[str]:
        """Return the cases the caller's person belongs to."""

    async def create_approval(self, **fields: Any) -> str:
        """Register a draft for the caller's person to confirm."""

    async def audit(self, **fields: Any) -> None:
        """Append an audit record."""

    async def raise_data_class(self, agent_id: str, data_class: str, system: str) -> None:
        """Mark the caller's session as holding data of this class."""


def token_from_request() -> str:
    """Return the bearer token of the MCP request being served."""
    access = get_access_token()
    if access is None:
        raise AuthenticationError("no agent token on this request")
    return access.token


class SystemsClient:
    """HTTP access to the corporate systems, as a platform service."""

    def __init__(
        self, base_url: str, internal_token: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._headers = {"Authorization": f"Bearer {internal_token}"}

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"corporate system unreachable: {exc}") from exc
        if response.status_code == 404:
            raise NotFoundError(str(response.json().get("detail", "not found")))
        if response.status_code >= 400:
            raise UpstreamError(str(response.json().get("detail", response.text[:200])))
        return response.json()

    async def get(self, path: str, **params: Any) -> Any:
        """GET a resource of a corporate system."""
        return await self._send(
            "GET", path, params={k: v for k, v in params.items() if v is not None}
        )

    async def post(self, path: str, body: Result) -> Any:
        """POST to a corporate system."""
        return await self._send("POST", path, json=body)


@dataclass
class Deps:
    """What a connector's tools need."""

    system: str
    control: Control
    systems: SystemsClient
    #: Where the caller's token comes from. Tests replace it.
    token: Callable[[], str] = token_from_request

    async def allow(self, tool: str, *, case_id: str | None = None, record: str = "") -> Decision:
        """Check that the caller may use ``tool`` on a record of ``case_id``; raise if not."""
        decision = await self.control.authorize(
            self.token(), self.system, tool, case_id=case_id, record=record
        )
        if not decision.allowed:
            raise AccessDeniedError(decision.reason)
        return decision

    async def register_draft(self, decision: Decision, draft: Result) -> Result:
        """Hand a draft to the caller's person for confirmation and describe it to the agent."""
        payload = draft["payload"]
        body = str(
            payload.get("text") or payload.get("justification") or payload.get("reason") or ""
        )
        approval_id = await self.control.create_approval(
            agent_id=decision.identity.agent_id,
            system=self.system,
            ref=draft["id"],
            title=draft["title"],
            body=body,
            case_id=draft.get("case_id"),
            detail={
                "kind": draft["kind"],
                **{
                    k: v for k, v in payload.items() if k not in ("text", "justification", "reason")
                },
            },
        )
        return {
            "status": "draft",
            "draft_id": draft["id"],
            "approval_id": approval_id,
            "title": draft["title"],
            "message": (
                f"Prepared as a draft. Nothing has changed in {self.system} yet: "
                f"{decision.identity.person_name or 'your person'} must confirm it "
                "on their approvals page."
            ),
        }


def safe(tool: Tool) -> Tool:
    """Turn refusals and system errors into results the agent can read and report."""

    @functools.wraps(tool)
    async def wrapper(*args: Any, **kwargs: Any) -> Result:
        try:
            return await tool(*args, **kwargs)
        except AccessDeniedError as exc:
            return {"error": "access denied", "reason": str(exc)}
        except NotFoundError as exc:
            return {"error": "not found", "reason": str(exc)}
        except UpstreamError as exc:
            log.warning("%s failed: %s", tool.__name__, exc)
            return {"error": "system error", "reason": str(exc)}

    return wrapper


class ControlVerifier:
    """Rejects, at the transport, any request whose token is not a live agent's."""

    def __init__(self, control: Any) -> None:
        self._control = control

    async def verify_token(self, token: str) -> AccessToken | None:
        """Resolve the token through the control plane."""
        try:
            identity, _ = await self._control.resolve(token, "connector")
        except (AuthenticationError, UpstreamError):
            return None
        return AccessToken(
            token=token,
            client_id=identity.agent_id,
            scopes=[],
            subject=identity.person_id or identity.agent_id,
        )


def build_server(
    name: str, instructions: str, tools: list[Tool], *, control: Any, public_url: str
) -> MCPServer:
    """Create the MCP server of one connector with its tools registered."""
    server = MCPServer(
        name,
        instructions=instructions,
        token_verifier=ControlVerifier(control),
        auth=AuthSettings(issuer_url=public_url, resource_server_url=f"{public_url}/mcp"),
    )
    for tool in tools:
        server.tool()(tool)
    return server
