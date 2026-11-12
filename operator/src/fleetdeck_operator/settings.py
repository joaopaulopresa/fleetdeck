"""Typed settings of the operator."""

from functools import lru_cache
from pathlib import Path

from fleetdeck_common.settings import CommonSettings


class OperatorSettings(CommonSettings):
    """Operator settings, read from ``FLEET_*`` environment variables."""

    hermes_image: str = "nousresearch/hermes-agent:stable"
    bundles_dir: Path = Path("/app/bundles")
    #: Base URL of the model gateway as seen from an agent pod.
    gateway_url: str = "http://model-gateway.fleet-system.svc.cluster.local:4000"
    #: Template of a connector's MCP URL; ``{name}`` is the connector name.
    connector_url_template: str = "http://connector-{name}.fleet-system.svc.cluster.local:8000/mcp"
    model_alias: str = "fleet-default"
    model_context_length: int = 200_000
    state_storage: str = "1Gi"
    cpu_request: str = "100m"
    memory_request: str = "320Mi"
    memory_limit: str = "768Mi"
    termination_grace_seconds: int = 15


@lru_cache
def get_settings() -> OperatorSettings:
    """Return the process-wide settings instance."""
    return OperatorSettings()
