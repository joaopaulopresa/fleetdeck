"""Typed settings of the connectors."""

from functools import lru_cache

from fleetdeck_common.settings import CommonSettings


class ConnectorSettings(CommonSettings):
    """Connector settings, read from ``FLEET_*`` environment variables."""

    systems_url: str = "http://mock-systems:8000"
    #: The connector's own URL as agents see it (reported in MCP auth metadata).
    public_url: str = "http://localhost:8000"
    port: int = 8000
    #: Used by the knowledge connector only: a login that is subject to row-level security.
    scoped_database_url: str = ""


@lru_cache
def get_settings() -> ConnectorSettings:
    """Return the process-wide settings instance."""
    return ConnectorSettings()
