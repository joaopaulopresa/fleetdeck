import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fleetdeck_common.errors import AuthenticationError
from fleetdeck_common.identity import Identity, RunContext
from fleetdeck_egress.proxy import EgressProxy, host_allowed, parse_request
from fleetdeck_egress.settings import EgressSettings


class FakeControl:
    def __init__(self) -> None:
        self.audits: list[dict[str, Any]] = []

    async def resolve(self, token: str, kind: str) -> tuple[Identity, RunContext]:
        if token != "good-token" or kind != "connector":
            raise AuthenticationError("no")
        identity = Identity(
            agent_id="marcus-reyes",
            person_id="marcus-reyes",
            person_name="Marcus Reyes",
            role="biller",
            department="Revenue Cycle",
        )
        return identity, RunContext()

    async def audit(self, **fields: Any) -> None:
        self.audits.append(fields)


def test_allow_list_matching() -> None:
    allow = ["www.cms.gov", ".irs.gov"]
    assert host_allowed("www.cms.gov", allow) and host_allowed("WWW.CMS.GOV.", allow)
    assert host_allowed("apps.irs.gov", allow) and host_allowed("irs.gov", allow)
    assert not host_allowed("cms.gov", allow)
    assert not host_allowed("evil-irs.gov", allow)
    assert not host_allowed("www.cms.gov.evil.example", allow)
    assert not host_allowed("anything.example", [])


def test_request_parsing() -> None:
    head = b"CONNECT www.cms.gov:443 HTTP/1.1\r\nProxy-Authorization: Bearer abc\r\n\r\n"
    parsed = parse_request(head)
    assert parsed is not None
    assert (parsed.method, parsed.host, parsed.port, parsed.token) == (
        "CONNECT",
        "www.cms.gov",
        443,
        "abc",
    )
    basic = b"CONNECT a.b:443 HTTP/1.1\r\nProxy-Authorization: Basic YWdlbnQ6dG9r\r\n\r\n"
    assert parse_request(basic).token == "tok"  # type: ignore[union-attr]
    plain = parse_request(b"GET http://example.org/ HTTP/1.1\r\n\r\n")
    assert plain is not None and plain.method == "GET"
    assert parse_request(b"garbage\r\n\r\n") is None


@pytest.fixture
async def target() -> AsyncIterator[int]:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"hello from target:" + await reader.read(5))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()


async def _connect(proxy_port: int, request: bytes, payload: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    if payload and b" 200 " in head:
        writer.write(payload)
        await writer.drain()
    rest = await reader.read()
    writer.close()
    return head + rest


@pytest.fixture
async def proxy(target: int) -> AsyncIterator[tuple[int, FakeControl, int]]:
    control = FakeControl()
    settings = EgressSettings(allowed_hosts="127.0.0.1", allowed_ports=str(target))
    server = await asyncio.start_server(EgressProxy(settings, control).handle, "127.0.0.1", 0)  # type: ignore[arg-type]
    yield server.sockets[0].getsockname()[1], control, target
    server.close()


async def test_allowed_destination_is_tunnelled_and_audited(
    proxy: tuple[int, FakeControl, int],
) -> None:
    port, control, target = proxy
    request = (
        f"CONNECT 127.0.0.1:{target} HTTP/1.1\r\nProxy-Authorization: Bearer good-token\r\n\r\n"
    ).encode()
    answer = await _connect(port, request, b"ping!")
    assert answer.startswith(b"HTTP/1.1 200") and answer.endswith(b"hello from target:ping!")
    assert control.audits == [
        {
            "kind": "egress",
            "action": f"127.0.0.1:{target}",
            "outcome": "allowed",
            "agent_id": "marcus-reyes",
            "system": "egress-proxy",
            "summary": f"Allowed outbound connection to 127.0.0.1:{target}",
        }
    ]


async def test_destination_off_the_list_is_refused_and_audited(
    proxy: tuple[int, FakeControl, int],
) -> None:
    port, control, target = proxy
    for destination in ("example.org:443", f"localhost:{target}", "127.0.0.1:22"):
        request = (
            f"CONNECT {destination} HTTP/1.1\r\nProxy-Authorization: Bearer good-token\r\n\r\n"
        ).encode()
        assert (await _connect(port, request)).startswith(b"HTTP/1.1 403")
    assert [a["outcome"] for a in control.audits] == ["denied"] * 3
    assert control.audits[0]["agent_id"] == "marcus-reyes"


async def test_no_token_wrong_token_and_plain_http_are_refused(
    proxy: tuple[int, FakeControl, int],
) -> None:
    port, control, target = proxy
    anonymous = f"CONNECT 127.0.0.1:{target} HTTP/1.1\r\n\r\n".encode()
    assert (await _connect(port, anonymous)).startswith(b"HTTP/1.1 407")
    wrong = (
        f"CONNECT 127.0.0.1:{target} HTTP/1.1\r\nProxy-Authorization: Bearer stolen\r\n\r\n"
    ).encode()
    assert (await _connect(port, wrong)).startswith(b"HTTP/1.1 407")
    plain = (
        f"GET http://127.0.0.1:{target}/ HTTP/1.1\r\nProxy-Authorization: Bearer good-token\r\n\r\n"
    ).encode()
    assert (await _connect(port, plain)).startswith(b"HTTP/1.1 403")
    assert [a["outcome"] for a in control.audits] == ["denied"]
