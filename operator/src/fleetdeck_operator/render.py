"""Renders the Kubernetes objects of one agent from the template and its role bundle."""

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from string import Template
from typing import Any

import yaml
from fleetdeck_common.bundles import Bundle
from fleetdeck_common.naming import namespace_for

from fleetdeck_operator.settings import OperatorSettings

Manifest = dict[str, Any]


@dataclass(frozen=True)
class AgentInput:
    """What the operator knows about an agent when it renders it."""

    agent_id: str
    owner_uid: str
    display_name: str
    person_name: str | None
    role: str
    department: str | None
    awake: bool
    suspended: bool
    #: ``FLEET_MODEL_KEY``, ``FLEET_CONNECTOR_TOKEN`` and ``API_SERVER_KEY`` for the pod.
    keys: dict[str, str]
    #: Approved personal and shared skills from the Agent resource: name, description, body.
    extra_skills: tuple[tuple[str, str, str], ...] = ()


def connector_server_name(connector: str) -> str:
    """Return the MCP server name Hermes uses for a connector (tool prefix ``mcp__<name>__``)."""
    return connector.replace("-", "_")


def hermes_config(bundle: Bundle, settings: OperatorSettings) -> dict[str, Any]:
    """Build the agent's Hermes ``config.yaml``.

    Secrets are referenced as ``${ENV}`` and resolved by Hermes from the pod environment, so the
    ConfigMap never contains a key.
    """
    return {
        "model": {
            "default": settings.model_alias,
            "provider": "custom",
            "base_url": f"{settings.gateway_url}/v1",
            "api_key": "${FLEET_MODEL_KEY}",
            "context_length": settings.model_context_length,
        },
        "mcp_servers": {
            connector_server_name(name): {
                "url": settings.connector_url_template.format(name=name),
                "headers": {"Authorization": "Bearer ${FLEET_CONNECTOR_TOKEN}"},
                # Connectors expose tools only; skip Hermes's resource and prompt wrappers.
                "tools": {"resources": False, "prompts": False},
            }
            for name in bundle.connectors
        },
        "agent": {"disabled_toolsets": list(bundle.disabled_toolsets)},
        "skills": {
            # Personal skill writes are staged for review instead of applied.
            "creation_nudge_interval": 0,
            "write_approval": True,
            "external_dirs": ["/bundle/skills"],
        },
    }


def skill_document(name: str, description: str, body: str) -> str:
    """Return a skill in the format Hermes loads: front matter, then the procedure."""
    return f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{body.strip()}\n"


def brief_document(agent: AgentInput, bundle: Bundle) -> str:
    """Return the agent's identity file: who it works for, then the role brief."""
    owner = agent.person_name or agent.display_name
    header = (
        f"You are the personal work agent of {owner} ({agent.role}"
        f"{', ' + agent.department if agent.department else ''}) at Meridian Health Group.\n\n"
    )
    return header + bundle.brief.strip() + "\n"


def _bundle_files(agent: AgentInput, bundle: Bundle, settings: OperatorSettings) -> dict[str, str]:
    """Return ConfigMap key -> content. Keys cannot contain ``/``; paths are mapped on mount."""
    files = {
        "config.yaml": yaml.safe_dump(hermes_config(bundle, settings), sort_keys=False),
        "SOUL.md": brief_document(agent, bundle),
    }
    for skill in bundle.skills:
        files[f"skill.{skill.name}"] = skill_document(skill.name, skill.description, skill.body)
    for name, description, body in agent.extra_skills:
        # A role bundle's skill wins over an approved skill of the same name.
        files.setdefault(f"skill.{name}", skill_document(name, description, body))
    return files


def _bundle_items(files: dict[str, str]) -> list[dict[str, str]]:
    items = []
    for key in files:
        path = f"skills/{key.removeprefix('skill.')}/SKILL.md" if key.startswith("skill.") else key
        items.append({"key": key, "path": path})
    return items


def render_agent(agent: AgentInput, bundle: Bundle, settings: OperatorSettings) -> list[Manifest]:
    """Return every object of the agent's namespace, the namespace first."""
    namespace = namespace_for(agent.agent_id, settings)
    files = _bundle_files(agent, bundle, settings)
    config_hash = hashlib.sha256(
        json.dumps([files, sorted(agent.keys.items())], sort_keys=True).encode()
    ).hexdigest()[:16]
    template = resources.files("fleetdeck_operator").joinpath("templates/agent.yaml").read_text()
    text = Template(template).substitute(
        NAMESPACE=namespace,
        AGENT_ID=agent.agent_id,
        OWNER_UID=agent.owner_uid,
        SYSTEM_NAMESPACE=settings.system_namespace,
        HERMES_PORT=settings.hermes_port,
        HERMES_IMAGE=settings.hermes_image,
        REPLICAS=1 if agent.awake and not agent.suspended else 0,
        CONFIG_HASH=config_hash,
        STATE_STORAGE=settings.state_storage,
        CPU_REQUEST=settings.cpu_request,
        MEMORY_REQUEST=settings.memory_request,
        MEMORY_LIMIT=settings.memory_limit,
        TERMINATION_GRACE=settings.termination_grace_seconds,
        BUNDLE_ITEMS=json.dumps(_bundle_items(files)),
    )
    manifests: list[Manifest] = [doc for doc in yaml.safe_load_all(text) if doc]
    generated: list[Manifest] = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "agent-keys", "namespace": namespace},
            "type": "Opaque",
            "stringData": dict(agent.keys),
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "agent-bundle", "namespace": namespace},
            "data": files,
        },
    ]
    # Namespace first, then what the Deployment needs, then the rest in template order.
    return [manifests[0], *generated, *manifests[1:]]


def phase_for(*, awake: bool, suspended: bool, ready: bool) -> str:
    """Return the phase the operator reports in the Agent status."""
    if suspended:
        return "Suspended"
    if not awake:
        return "Asleep"
    return "Active" if ready else "Waking"
