"""Typed settings shared by all services. Each service extends :class:`CommonSettings`."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    """Settings every Fleetdeck service reads from ``FLEET_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="FLEET_", extra="ignore")

    log_level: str = "INFO"
    system_namespace: str = "fleet-system"
    #: Holds only the agents' keys; the provider key is not readable from it.
    keys_namespace: str = "fleet-keys"
    agent_namespace_prefix: str = "agent-"
    cluster_domain: str = "cluster.local"
    hermes_port: int = 8642
    #: Shared secret that platform services present to the control plane's internal API.
    internal_token: str = ""
    control_plane_url: str = "http://control-plane:8000"
