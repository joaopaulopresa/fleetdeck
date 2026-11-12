"""The control plane's view of the Kubernetes cluster.

The control plane never creates pods or namespaces. It writes two things: the ``Agent`` custom
resource (what should exist, and whether it should be awake) and the Secret that holds an
agent's keys. The operator turns those into workloads. This module is that interface; tests use
:class:`FakeAgentCluster`.
"""

import asyncio
import base64
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from fleetdeck_common.errors import NotFoundError, UpstreamError
from fleetdeck_common.naming import keys_secret_name

GROUP = "fleetdeck.dev"
VERSION = "v1alpha1"
PLURAL = "agents"


@dataclass(frozen=True)
class AgentSpec:
    """Desired state of one agent, as written to its custom resource."""

    agent_id: str
    display_name: str
    person_id: str | None
    person_name: str | None
    role: str
    department: str | None
    bundle: str
    tier: str = "strict"
    awake: bool = False
    suspended: bool = False


@dataclass(frozen=True)
class AgentKeys:
    """The three secrets of an agent. They exist in plain text only in Kubernetes Secrets."""

    model_key: str
    connector_token: str
    runs_key: str


@dataclass(frozen=True)
class ClusterStatus:
    """What the operator last reported for an agent."""

    phase: str
    ready: bool
    message: str = ""


class AgentCluster(Protocol):
    """Operations the control plane needs from the cluster."""

    async def apply(self, spec: AgentSpec) -> None:
        """Create or update the agent's custom resource."""

    async def delete(self, agent_id: str) -> None:
        """Delete the custom resource; the operator removes the namespace."""

    async def set_awake(self, agent_id: str, awake: bool) -> None:
        """Ask for the agent's pod to run (``True``) or to be scaled to zero."""

    async def status(self, agent_id: str) -> ClusterStatus | None:
        """Return the operator's last report, or ``None`` if the agent does not exist."""

    async def write_keys(self, agent_id: str, keys: AgentKeys) -> None:
        """Store the agent's keys in the system namespace for the operator to copy."""

    async def read_keys(self, agent_id: str) -> AgentKeys:
        """Read the agent's keys back."""

    async def delete_keys(self, agent_id: str) -> None:
        """Remove the agent's keys."""


def _body(spec: AgentSpec) -> dict[str, Any]:
    return {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "Agent",
        "metadata": {"name": spec.agent_id},
        "spec": {
            "displayName": spec.display_name,
            "personId": spec.person_id,
            "personName": spec.person_name,
            "role": spec.role,
            "department": spec.department,
            "bundle": spec.bundle,
            "tier": spec.tier,
            "awake": spec.awake,
            "suspended": spec.suspended,
            "keysSecret": keys_secret_name(spec.agent_id),
        },
    }


class KubernetesAgentCluster:
    """:class:`AgentCluster` backed by the Kubernetes API."""

    def __init__(self, system_namespace: str, *, in_cluster: bool) -> None:
        from kubernetes import client, config

        if in_cluster:
            config.load_incluster_config()  # type: ignore[no-untyped-call]
        else:
            config.load_kube_config()  # type: ignore[no-untyped-call]
        self._namespace = system_namespace
        self._custom = client.CustomObjectsApi()
        self._core = client.CoreV1Api()
        self._api_exception = client.ApiException

    async def _call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except self._api_exception as exc:
            if exc.status == 404:
                raise NotFoundError(str(exc.reason)) from exc
            raise UpstreamError(f"kubernetes api: {exc.status} {exc.reason}") from exc

    async def apply(self, spec: AgentSpec) -> None:
        """Create the custom resource, or patch its spec if it exists."""
        body = _body(spec)
        try:
            await self._call(
                self._custom.get_cluster_custom_object, GROUP, VERSION, PLURAL, spec.agent_id
            )
        except NotFoundError:
            await self._call(
                self._custom.create_cluster_custom_object, GROUP, VERSION, PLURAL, body
            )
            return
        await self._call(
            self._custom.patch_cluster_custom_object,
            GROUP,
            VERSION,
            PLURAL,
            spec.agent_id,
            {"spec": body["spec"]},
        )

    async def delete(self, agent_id: str) -> None:
        """Delete the custom resource if it exists."""
        try:
            await self._call(
                self._custom.delete_cluster_custom_object, GROUP, VERSION, PLURAL, agent_id
            )
        except NotFoundError:
            return

    async def set_awake(self, agent_id: str, awake: bool) -> None:
        """Patch ``spec.awake``."""
        await self._call(
            self._custom.patch_cluster_custom_object,
            GROUP,
            VERSION,
            PLURAL,
            agent_id,
            {"spec": {"awake": awake}},
        )

    async def status(self, agent_id: str) -> ClusterStatus | None:
        """Read ``status`` from the custom resource."""
        try:
            obj = await self._call(
                self._custom.get_cluster_custom_object, GROUP, VERSION, PLURAL, agent_id
            )
        except NotFoundError:
            return None
        status = obj.get("status") or {}
        return ClusterStatus(
            phase=str(status.get("phase", "Pending")),
            ready=bool(status.get("ready", False)),
            message=str(status.get("message", "")),
        )

    async def write_keys(self, agent_id: str, keys: AgentKeys) -> None:
        """Create or replace the keys Secret."""
        from kubernetes import client

        name = keys_secret_name(agent_id)
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, labels={"fleetdeck.dev/agent-keys": agent_id}),
            string_data=asdict(keys),
        )
        try:
            await self._call(self._core.create_namespaced_secret, self._namespace, secret)
        except UpstreamError:
            await self._call(self._core.replace_namespaced_secret, name, self._namespace, secret)

    async def read_keys(self, agent_id: str) -> AgentKeys:
        """Read and decode the keys Secret."""
        secret = await self._call(
            self._core.read_namespaced_secret, keys_secret_name(agent_id), self._namespace
        )
        data = {k: base64.b64decode(v).decode() for k, v in (secret.data or {}).items()}
        return AgentKeys(**data)

    async def delete_keys(self, agent_id: str) -> None:
        """Delete the keys Secret if it exists."""
        try:
            await self._call(
                self._core.delete_namespaced_secret, keys_secret_name(agent_id), self._namespace
            )
        except NotFoundError:
            return


class FakeAgentCluster:
    """In-memory :class:`AgentCluster` for tests and for running the API without a cluster."""

    def __init__(self) -> None:
        self.specs: dict[str, AgentSpec] = {}
        self.keys: dict[str, AgentKeys] = {}

    async def apply(self, spec: AgentSpec) -> None:
        """Remember the spec."""
        self.specs[spec.agent_id] = spec

    async def delete(self, agent_id: str) -> None:
        """Forget the agent."""
        self.specs.pop(agent_id, None)

    async def set_awake(self, agent_id: str, awake: bool) -> None:
        """Flip ``awake`` on the remembered spec."""
        spec = self.specs.get(agent_id)
        if spec is None:
            raise NotFoundError(agent_id)
        self.specs[agent_id] = AgentSpec(**{**asdict(spec), "awake": awake})

    async def status(self, agent_id: str) -> ClusterStatus | None:
        """Report the pod as ready exactly when the spec says awake."""
        spec = self.specs.get(agent_id)
        if spec is None:
            return None
        running = spec.awake and not spec.suspended
        return ClusterStatus(phase="Active" if running else "Asleep", ready=running)

    async def write_keys(self, agent_id: str, keys: AgentKeys) -> None:
        """Remember the keys."""
        self.keys[agent_id] = keys

    async def read_keys(self, agent_id: str) -> AgentKeys:
        """Return the remembered keys."""
        try:
            return self.keys[agent_id]
        except KeyError as exc:
            raise NotFoundError(agent_id) from exc

    async def delete_keys(self, agent_id: str) -> None:
        """Forget the keys."""
        self.keys.pop(agent_id, None)
