"""An HTTPS CONNECT proxy that knows who is asking and where they may go.

Agent pods have no route to the internet. The only way out is this proxy, which demands the
agent's own connector token, tunnels only to allow-listed hosts and writes every attempt,
allowed or refused, to the audit trail. Plain HTTP is refused.
"""

import asyncio
import base64
import contextlib
import logging
from dataclasses import dataclass

from fleetdeck_common.control_client import ControlPlaneClient
from fleetdeck_common.errors import AuthenticationError, UpstreamError
from fleetdeck_common.logs import configure_logging
from prometheus_client import Counter, start_http_server

from fleetdeck_egress.settings import EgressSettings, get_settings

log = logging.getLogger(__name__)

ATTEMPTS = Counter("fleetdeck_egress_total", "Egress attempts by agents", ["outcome"])
_MAX_HEADER_BYTES = 16_384


def host_allowed(host: str, allow_list: list[str]) -> bool:
    """Return whether ``host`` is on the allow-list.

    An entry matches by exact name; an entry with a leading dot also matches every subdomain.
    """
    host = host.lower().rstrip(".")
    for entry in allow_list:
        if entry.startswith("."):
            if host.endswith(entry) or host == entry[1:]:
                return True
        elif host == entry:
            return True
    return False


@dataclass(frozen=True)
class ConnectRequest:
    """A parsed ``CONNECT host:port`` request."""

    method: str
    host: str
    port: int
    token: str | None


def parse_request(head: bytes) -> ConnectRequest | None:
    """Parse the request line and the proxy credentials from the request head."""
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
    except ValueError:
        return None
    host, _, port = target.rpartition(":")
    token: str | None = None
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() != "proxy-authorization":
            continue
        scheme, _, credentials = value.strip().partition(" ")
        if scheme.lower() == "bearer":
            token = credentials.strip()
        elif scheme.lower() == "basic":
            with contextlib.suppress(ValueError):
                # curl -U agent:<token>: the password is the token.
                token = base64.b64decode(credentials).decode().partition(":")[2] or None
    if method.upper() != "CONNECT" or not host or not port.isdigit():
        return ConnectRequest(method.upper(), host or target, 0, token)
    return ConnectRequest("CONNECT", host.strip("[]"), int(port), token)


class EgressProxy:
    """The proxy server."""

    def __init__(self, settings: EgressSettings, control: ControlPlaneClient) -> None:
        self._settings = settings
        self._control = control

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one client connection."""
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
            return
        request = parse_request(head)
        try:
            await self._serve(request, reader, writer)
        except (ConnectionError, OSError):
            log.debug("connection dropped")
        finally:
            with contextlib.suppress(ConnectionError, OSError):
                writer.close()
                await writer.wait_closed()

    async def _refuse(self, writer: asyncio.StreamWriter, status: str, reason: str) -> None:
        extra = (
            'Proxy-Authenticate: Bearer realm="fleetdeck"\r\n' if status.startswith("407") else ""
        )
        body = reason.encode()
        writer.write(
            f"HTTP/1.1 {status}\r\n{extra}Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    async def _serve(
        self,
        request: ConnectRequest | None,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if request is None:
            await self._refuse(writer, "400 Bad Request", "malformed request")
            return
        if request.token is None:
            ATTEMPTS.labels(outcome="unauthenticated").inc()
            await self._refuse(writer, "407 Proxy Authentication Required", "agent token required")
            return
        try:
            identity, _ = await self._control.resolve(request.token, "connector")
        except AuthenticationError:
            ATTEMPTS.labels(outcome="unauthenticated").inc()
            await self._refuse(writer, "407 Proxy Authentication Required", "unknown agent token")
            return
        except UpstreamError:
            await self._refuse(writer, "503 Service Unavailable", "identity service unavailable")
            return

        destination = f"{request.host}:{request.port}"
        allowed = (
            request.method == "CONNECT"
            and request.port in self._settings.ports
            and host_allowed(request.host, self._settings.hosts)
        )
        ATTEMPTS.labels(outcome="allowed" if allowed else "denied").inc()
        with contextlib.suppress(UpstreamError):
            await self._control.audit(
                kind="egress",
                action=destination,
                outcome="allowed" if allowed else "denied",
                agent_id=identity.agent_id,
                system="egress-proxy",
                summary=f"{'Allowed' if allowed else 'Refused'} outbound connection to "
                f"{destination}",
            )
        if not allowed:
            log.info("refused %s -> %s", identity.agent_id, destination)
            await self._refuse(writer, "403 Forbidden", "destination is not on the allow-list")
            return

        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(request.host, request.port),
                timeout=self._settings.connect_timeout_seconds,
            )
        except (OSError, TimeoutError):
            await self._refuse(writer, "502 Bad Gateway", "destination unreachable")
            return
        log.info("tunnel %s -> %s", identity.agent_id, destination)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _pipe(reader, upstream_writer),
            _pipe(upstream_reader, writer),
            return_exceptions=True,
        )


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    finally:
        with contextlib.suppress(ConnectionError, OSError):
            writer.close()


async def serve(settings: EgressSettings | None = None) -> None:
    """Run the proxy until cancelled."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    proxy = EgressProxy(settings, ControlPlaneClient(settings))
    server = await asyncio.start_server(
        proxy.handle, settings.listen_host, settings.listen_port, limit=_MAX_HEADER_BYTES
    )
    start_http_server(settings.metrics_port)
    log.info("egress proxy on :%d, allow-list: %s", settings.listen_port, settings.hosts or "empty")
    async with server:
        await server.serve_forever()


def main() -> None:
    """Entry point of ``fleetdeck-egress``."""
    asyncio.run(serve())
