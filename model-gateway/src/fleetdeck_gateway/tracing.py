"""Tracing of model calls. The gateway sees every call, so traces are emitted here."""

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from fleetdeck_common.identity import Identity, RunContext

from fleetdeck_gateway.routing import Route
from fleetdeck_gateway.usage import Usage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallRecord:
    """Everything known about one finished model call."""

    identity: Identity
    context: RunContext
    route: Route
    request: dict[str, Any]
    usage: Usage
    status: int
    latency_ms: int


class Tracer(Protocol):
    """Receives finished calls."""

    def record(self, call: CallRecord) -> None:
        """Emit a trace for the call. Must not raise."""

    def shutdown(self) -> None:
        """Flush pending traces."""


class NoopTracer:
    """Used when no tracing backend is configured."""

    def record(self, call: CallRecord) -> None:
        """Do nothing."""

    def shutdown(self) -> None:
        """Do nothing."""
