"""The exception hierarchy used across Fleetdeck services.

Every error a service raises on purpose derives from :class:`FleetdeckError`, so API layers can
map the whole family to HTTP responses in one place.
"""


class FleetdeckError(Exception):
    """Base class for all errors raised deliberately by Fleetdeck code."""


class NotFoundError(FleetdeckError):
    """The requested object does not exist, or the caller may not know that it exists."""


class ConflictError(FleetdeckError):
    """The request conflicts with the current state (duplicate, wrong status)."""


class AuthenticationError(FleetdeckError):
    """The caller did not prove who it is."""


class PermissionDeniedError(FleetdeckError):
    """The caller is known but is not allowed to do this."""


class PolicyDeniedError(PermissionDeniedError):
    """A collaboration policy refused a request between two agents."""


class AgentUnavailableError(FleetdeckError):
    """The agent could not be woken or did not answer in time."""


class UpstreamError(FleetdeckError):
    """A system Fleetdeck depends on (model provider, Hermes, corporate system) failed."""
