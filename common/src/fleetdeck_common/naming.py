"""How agent ids map to Kubernetes names. Kept in one place so every service agrees."""

import re

from fleetdeck_common.settings import CommonSettings

_AGENT_ID = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")

#: Name of the Deployment, Service and container in every agent namespace.
HERMES_NAME = "hermes"


def validate_agent_id(agent_id: str) -> str:
    """Return ``agent_id`` if it is a valid DNS label short enough for a namespace name."""
    if not _AGENT_ID.fullmatch(agent_id):
        raise ValueError(f"invalid agent id: {agent_id!r}")
    return agent_id


def namespace_for(agent_id: str, settings: CommonSettings) -> str:
    """Return the namespace that holds the agent's pod, volume and secrets."""
    return f"{settings.agent_namespace_prefix}{validate_agent_id(agent_id)}"


def hermes_url(agent_id: str, settings: CommonSettings) -> str:
    """Return the in-cluster base URL of the agent's Hermes API."""
    host = f"{HERMES_NAME}.{namespace_for(agent_id, settings)}.svc.{settings.cluster_domain}"
    return f"http://{host}:{settings.hermes_port}"


def keys_secret_name(agent_id: str) -> str:
    """Return the name of the Secret in the system namespace that holds an agent's keys."""
    return f"agent-keys-{validate_agent_id(agent_id)}"
