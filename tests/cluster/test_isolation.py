"""Each test tries to cross one boundary of an agent's sandbox and expects to be refused."""

import pytest

from .conftest import Api, can_i, kubectl, kubectl_json, run_in_agent

pytestmark = pytest.mark.cluster

PROBE = """
import socket, sys
for target in sys.argv[1:]:
    host, port = target.rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=4).close()
        print(target, "REACHABLE")
    except Exception as exc:
        print(target, "blocked", type(exc).__name__)
"""


def probe(agent_id: str, *targets: str) -> dict[str, bool]:
    lines = run_in_agent(agent_id, PROBE, *targets).splitlines()
    return {line.split()[0]: line.split()[1] == "REACHABLE" for line in lines}


@pytest.fixture(scope="module")
def awake(api: Api, admin: dict[str, str], throwaway: str) -> str:
    api.wake(throwaway, admin)
    api.wake("elena-park", admin)
    return throwaway


def test_new_agent_gets_its_own_locked_down_namespace_and_starts_asleep(throwaway: str) -> None:
    namespace = kubectl_json("get", "namespace", f"agent-{throwaway}")
    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert namespace["metadata"]["ownerReferences"][0]["kind"] == "Agent"
    policies = kubectl("-n", f"agent-{throwaway}", "get", "networkpolicy", "-o", "name")
    assert "default-deny" in policies and "platform-only" in policies


def test_agent_cannot_reach_another_agent(awake: str) -> None:
    other_ip = kubectl(
        "-n",
        "agent-elena-park",
        "get",
        "pod",
        "-l",
        "app=hermes",
        "-o",
        "jsonpath={.items[0].status.podIP}",
    )
    reached = probe(awake, "hermes.agent-elena-park.svc.cluster.local:8642", f"{other_ip}:8642")
    assert not any(reached.values()), reached
    # ...and the other agent cannot reach this one.
    own_ip = kubectl(
        "-n",
        f"agent-{awake}",
        "get",
        "pod",
        "-l",
        "app=hermes",
        "-o",
        "jsonpath={.items[0].status.podIP}",
    )
    reached = probe("elena-park", f"hermes.agent-{awake}.svc.cluster.local:8642", f"{own_ip}:8642")
    assert not any(reached.values()), reached


def test_agent_cannot_reach_the_internet_directly(awake: str) -> None:
    reached = probe(awake, "openrouter.ai:443", "1.1.1.1:443", "8.8.8.8:53")
    assert not any(reached.values()), reached


def test_agent_cannot_reach_the_kubernetes_api_or_the_node(awake: str) -> None:
    node_ip = kubectl(
        "get",
        "node",
        "-o",
        "jsonpath={.items[0].status.addresses[?(@.type=='InternalIP')].address}",
    )
    reached = probe(
        awake, "kubernetes.default.svc.cluster.local:443", f"{node_ip}:6443", f"{node_ip}:10250"
    )
    assert not any(reached.values()), reached


def test_agent_cannot_reach_the_database_or_the_control_plane(awake: str) -> None:
    reached = probe(
        awake,
        "postgres.fleet-system.svc.cluster.local:5432",
        "control-plane.fleet-system.svc.cluster.local:8000",
    )
    assert not any(reached.values()), reached


def test_agent_pod_holds_no_kubernetes_credentials_and_is_not_root(awake: str) -> None:
    out = run_in_agent(
        awake,
        """
import os
print(os.getuid(), os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token'),
      os.path.exists('/var/run/docker.sock'))
""",
    )
    assert out == "10000 False False"
    pod = kubectl_json("-n", f"agent-{awake}", "get", "pod", "-l", "app=hermes")["items"][0]
    assert pod["spec"]["automountServiceAccountToken"] is False
    for container in pod["spec"]["containers"]:
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert container["securityContext"]["allowPrivilegeEscalation"] is False


def test_agent_service_account_has_no_api_permissions(awake: str) -> None:
    account = f"agent-{awake}:agent"
    for verb, resource in (
        ("get", "pods"),
        ("get", "secrets"),
        ("list", "namespaces"),
        ("create", "pods"),
    ):
        assert not can_i(verb, resource, service_account=account, namespace=f"agent-{awake}")


def test_namespace_refuses_a_privileged_pod(awake: str) -> None:
    manifest = f"""
apiVersion: v1
kind: Pod
metadata: {{name: escape, namespace: agent-{awake}}}
spec:
  containers:
    - name: x
      image: nousresearch/hermes-agent:stable
      securityContext: {{privileged: true}}
      volumeMounts: [{{name: host, mountPath: /host}}]
  volumes: [{{name: host, hostPath: {{path: /}}}}]
"""
    out = kubectl("apply", "-f", "-", stdin=manifest, check=False)
    assert out == ""  # nothing was created
    pods = kubectl("-n", f"agent-{awake}", "get", "pods", "-o", "name")
    assert "escape" not in pods


def test_control_plane_cannot_read_the_provider_key_or_create_workloads() -> None:
    account = "fleet-system:control-plane"
    assert not can_i("get", "secrets", service_account=account, namespace="fleet-system")
    assert not can_i("create", "pods", service_account=account, namespace="fleet-system")
    assert not can_i("create", "deployments", service_account=account, namespace="agent-elena-park")
    assert can_i("get", "secrets", service_account=account, namespace="fleet-keys")
    assert can_i("patch", "agents.fleetdeck.dev", service_account=account)
