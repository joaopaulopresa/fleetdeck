"""Kopf handlers: reconcile ``Agent`` resources and mirror pod readiness into their status."""

import base64
import logging
from typing import Any, cast

import kopf
from fleetdeck_common.logs import configure_logging
from kubernetes import client, config, dynamic

from fleetdeck_operator.bundles import load_bundle
from fleetdeck_operator.render import AgentInput, Manifest, phase_for, render_agent
from fleetdeck_operator.settings import get_settings

GROUP, VERSION, PLURAL = "fleetdeck.dev", "v1alpha1", "agents"
FIELD_MANAGER = "fleetdeck-operator"
AGENT_LABEL = "fleetdeck.dev/agent-id"

log = logging.getLogger(__name__)
_dynamic: dynamic.DynamicClient | None = None


def _client() -> dynamic.DynamicClient:
    global _dynamic
    if _dynamic is None:
        _dynamic = dynamic.DynamicClient(client.ApiClient())  # type: ignore[no-untyped-call]
    return _dynamic


@kopf.on.startup()
def startup(settings: kopf.OperatorSettings, **_: Any) -> None:
    """Load cluster credentials and configure kopf."""
    configure_logging(get_settings().log_level)
    try:
        config.load_incluster_config()  # type: ignore[no-untyped-call]
    except config.ConfigException:
        config.load_kube_config()  # type: ignore[no-untyped-call]
    settings.persistence.finalizer = "fleetdeck.dev/operator"
    settings.posting.enabled = False
    settings.watching.server_timeout = 300


def apply_manifest(manifest: Manifest) -> None:
    """Server-side apply one object; safe to repeat."""
    api = _client()
    resource = api.resources.get(api_version=manifest["apiVersion"], kind=manifest["kind"])
    api.server_side_apply(  # type: ignore[no-untyped-call]
        resource,
        body=manifest,
        name=manifest["metadata"]["name"],
        namespace=manifest["metadata"].get("namespace"),
        field_manager=FIELD_MANAGER,
        force_conflicts=True,
    )


def read_keys(secret_name: str) -> dict[str, str]:
    """Read an agent's keys from the keys namespace and map them to pod environment names."""
    secret = client.CoreV1Api().read_namespaced_secret(secret_name, get_settings().keys_namespace)
    data = {key: base64.b64decode(value).decode() for key, value in (secret.data or {}).items()}
    return {
        "FLEET_MODEL_KEY": data["model_key"],
        "FLEET_CONNECTOR_TOKEN": data["connector_token"],
        "API_SERVER_KEY": data["runs_key"],
    }


def _ready(namespace: str) -> bool:
    try:
        deployment = client.AppsV1Api().read_namespaced_deployment("hermes", namespace)
    except client.ApiException:
        return False
    status = deployment.status
    return status is not None and (status.ready_replicas or 0) >= 1


@kopf.on.resume(GROUP, VERSION, PLURAL)
@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.update(GROUP, VERSION, PLURAL, field="spec")
def reconcile(name: str, uid: str, spec: kopf.Spec, patch: kopf.Patch, **_: Any) -> None:
    """Bring the agent's namespace in line with its spec."""
    settings = get_settings()
    try:
        bundle = load_bundle(settings.bundles_dir, str(spec["bundle"]))
        keys = read_keys(str(spec["keysSecret"]))
    except client.ApiException as exc:
        raise kopf.TemporaryError(f"keys secret not readable yet: {exc.reason}", delay=5) from exc
    agent = AgentInput(
        agent_id=name,
        owner_uid=uid,
        display_name=str(spec.get("displayName") or name),
        person_name=spec.get("personName"),
        role=str(spec.get("role") or "service"),
        department=spec.get("department"),
        awake=bool(spec.get("awake", False)),
        suspended=bool(spec.get("suspended", False)),
        keys=keys,
    )
    manifests = render_agent(agent, bundle, settings)
    for manifest in manifests:
        apply_manifest(manifest)
    namespace = manifests[0]["metadata"]["name"]
    ready = _ready(namespace)
    patch.status["namespace"] = namespace
    patch.status["ready"] = ready
    patch.status["phase"] = phase_for(awake=agent.awake, suspended=agent.suspended, ready=ready)
    patch.status["message"] = ""
    log.info("agent %s reconciled: awake=%s suspended=%s", name, agent.awake, agent.suspended)


@kopf.on.event("apps", "v1", "deployments", labels={AGENT_LABEL: kopf.PRESENT})
def mirror_readiness(body: kopf.Body, labels: kopf.Labels, **_: Any) -> None:
    """Copy the Hermes Deployment's readiness into the owning Agent's status."""
    agent_id = labels[AGENT_LABEL]
    custom = client.CustomObjectsApi()
    try:
        found = custom.get_cluster_custom_object(GROUP, VERSION, PLURAL, agent_id)
    except client.ApiException:
        return
    agent = cast("dict[str, Any]", found)
    spec = agent.get("spec", {})
    ready = int((body.get("status") or {}).get("readyReplicas") or 0) >= 1
    phase = phase_for(
        awake=bool(spec.get("awake")), suspended=bool(spec.get("suspended")), ready=ready
    )
    current = agent.get("status") or {}
    if current.get("phase") == phase and current.get("ready") == ready:
        return
    custom.patch_cluster_custom_object_status(
        GROUP, VERSION, PLURAL, agent_id, {"status": {"phase": phase, "ready": ready}}
    )
