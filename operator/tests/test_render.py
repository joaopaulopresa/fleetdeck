from pathlib import Path
from typing import Any

import pytest
import yaml
from fleetdeck_common.bundles import BRIEF_MAX_CHARS, Bundle, Skill, list_bundles
from fleetdeck_operator.render import AgentInput, Manifest, phase_for, render_agent
from fleetdeck_operator.settings import OperatorSettings
from pydantic import ValidationError

SETTINGS = OperatorSettings()
KEYS = {
    "FLEET_MODEL_KEY": "fdm_secret",
    "FLEET_CONNECTOR_TOKEN": "fdc_secret",
    "API_SERVER_KEY": "fdr_secret_0123456789",
}
BUNDLE = Bundle(
    name="biller",
    brief="Follow up on denials.",
    connectors=["billing", "patient-record"],
    skills=[Skill(name="appeal-letter", description="How to draft: an appeal", body="1. Read.")],
)


def agent(**overrides: Any) -> AgentInput:
    values: dict[str, Any] = {
        "agent_id": "marcus-reyes",
        "owner_uid": "uid-1",
        "display_name": "Marcus's agent",
        "person_name": "Marcus Reyes",
        "role": "biller",
        "department": "Revenue Cycle",
        "awake": False,
        "suspended": False,
        "keys": KEYS,
    }
    return AgentInput(**{**values, **overrides})


def by_kind(manifests: list[Manifest], kind: str, name: str | None = None) -> Manifest:
    return next(
        m
        for m in manifests
        if m["kind"] == kind and (name is None or m["metadata"]["name"] == name)
    )


def test_everything_lives_in_the_agents_own_namespace_owned_by_the_agent() -> None:
    manifests = render_agent(agent(), BUNDLE, SETTINGS)
    namespace = manifests[0]
    assert (
        namespace["kind"] == "Namespace" and namespace["metadata"]["name"] == "agent-marcus-reyes"
    )
    assert namespace["metadata"]["ownerReferences"][0]["uid"] == "uid-1"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    assert {m["metadata"]["namespace"] for m in manifests[1:]} == {"agent-marcus-reyes"}
    assert {m["kind"] for m in manifests} == {
        "Namespace",
        "Secret",
        "ConfigMap",
        "ServiceAccount",
        "NetworkPolicy",
        "PersistentVolumeClaim",
        "Service",
        "Deployment",
    }


def test_replicas_follow_awake_and_suspended() -> None:
    def replicas(**kw: Any) -> int:
        return int(
            by_kind(render_agent(agent(**kw), BUNDLE, SETTINGS), "Deployment")["spec"]["replicas"]
        )

    assert replicas() == 0
    assert replicas(awake=True) == 1
    assert replicas(awake=True, suspended=True) == 0


def test_pod_is_locked_down() -> None:
    manifests = render_agent(agent(awake=True), BUNDLE, SETTINGS)
    pod = by_kind(manifests, "Deployment")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert by_kind(manifests, "ServiceAccount")["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == 10000
    for container in (*pod["initContainers"], *pod["containers"]):
        context = container["securityContext"]
        assert context["allowPrivilegeEscalation"] is False
        assert context["capabilities"] == {"drop": ["ALL"]}
        assert not context.get("privileged")
    volumes = pod["volumes"]
    assert {next(iter(set(v) - {"name"})) for v in volumes} == {
        "persistentVolumeClaim",
        "configMap",
    }
    assert "docker.sock" not in yaml.safe_dump(manifests)
    assert "hostPath" not in yaml.safe_dump(manifests)


def test_network_policy_denies_by_default_and_names_the_only_peers() -> None:
    manifests = render_agent(agent(), BUNDLE, SETTINGS)
    deny = by_kind(manifests, "NetworkPolicy", "default-deny")["spec"]
    assert deny == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    allow = by_kind(manifests, "NetworkPolicy", "platform-only")["spec"]
    ingress_peer = allow["ingress"][0]["from"][0]
    assert ingress_peer["podSelector"]["matchLabels"] == {"fleetdeck.dev/calls-agents": "true"}
    assert ingress_peer["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "fleet-system"
    }
    egress_labels = [
        peer["podSelector"]["matchLabels"] for rule in allow["egress"] for peer in rule["to"]
    ]
    assert egress_labels == [{"fleetdeck.dev/agent-facing": "true"}, {"k8s-app": "kube-dns"}]


def test_keys_are_in_the_secret_and_never_in_the_config_map() -> None:
    manifests = render_agent(agent(), BUNDLE, SETTINGS)
    assert by_kind(manifests, "Secret")["stringData"] == KEYS
    config_map = yaml.safe_dump(by_kind(manifests, "ConfigMap"))
    for secret in KEYS.values():
        assert secret not in config_map
    config = yaml.safe_load(by_kind(manifests, "ConfigMap")["data"]["config.yaml"])
    assert config["model"]["api_key"] == "${FLEET_MODEL_KEY}"
    assert config["model"]["base_url"].endswith(":4000/v1")
    assert config["mcp_servers"]["patient_record"] == {
        "url": "http://connector-patient-record.fleet-system.svc.cluster.local:8000/mcp",
        "headers": {"Authorization": "Bearer ${FLEET_CONNECTOR_TOKEN}"},
        "tools": {"resources": False, "prompts": False},
    }


def test_hermes_cannot_act_outside_its_connectors_or_write_skills_unreviewed() -> None:
    config = yaml.safe_load(
        by_kind(render_agent(agent(), BUNDLE, SETTINGS), "ConfigMap")["data"]["config.yaml"]
    )
    assert {"terminal", "file", "web", "browser", "code_execution", "cronjob", "delegation"} <= set(
        config["agent"]["disabled_toolsets"]
    )
    assert config["skills"]["write_approval"] is True
    assert config["skills"]["creation_nudge_interval"] == 0


def test_brief_and_skills_are_mounted_where_hermes_reads_them() -> None:
    manifests = render_agent(agent(), BUNDLE, SETTINGS)
    data = by_kind(manifests, "ConfigMap")["data"]
    assert data["SOUL.md"].startswith("You are the personal work agent of Marcus Reyes (biller")
    assert "Follow up on denials." in data["SOUL.md"]
    skill = data["skill.appeal-letter"]
    front = yaml.safe_load(skill.split("---")[1])
    assert front == {"name": "appeal-letter", "description": "How to draft: an appeal"}
    pod = by_kind(manifests, "Deployment")["spec"]["template"]["spec"]
    items = next(v for v in pod["volumes"] if v["name"] == "bundle")["configMap"]["items"]
    assert {"key": "skill.appeal-letter", "path": "skills/appeal-letter/SKILL.md"} in items


def test_config_change_rolls_the_pod_but_waking_does_not() -> None:
    def config_hash(a: AgentInput, b: Bundle) -> str:
        deployment = by_kind(render_agent(a, b, SETTINGS), "Deployment")
        return str(
            deployment["spec"]["template"]["metadata"]["annotations"]["fleetdeck.dev/config-hash"]
        )

    assert config_hash(agent(), BUNDLE) == config_hash(agent(awake=True), BUNDLE)
    changed = BUNDLE.model_copy(update={"brief": "Different brief."})
    assert config_hash(agent(), BUNDLE) != config_hash(agent(), changed)


def test_phase() -> None:
    assert phase_for(awake=False, suspended=False, ready=False) == "Asleep"
    assert phase_for(awake=True, suspended=False, ready=False) == "Waking"
    assert phase_for(awake=True, suspended=False, ready=True) == "Active"
    assert phase_for(awake=True, suspended=True, ready=True) == "Suspended"


def test_brief_over_the_context_file_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Bundle(name="x", brief="a" * (BRIEF_MAX_CHARS + 1))


def test_every_shipped_bundle_loads_and_renders() -> None:
    bundles = list_bundles(Path(__file__).parents[2] / "bundles")
    assert {b.name for b in bundles} == {"physician", "biller", "lawyer", "tax-analyst", "director"}
    for bundle in bundles:
        assert render_agent(agent(), bundle, SETTINGS)


def test_approved_skills_are_mounted_but_never_replace_a_bundle_skill() -> None:
    extra = (
        ("payer-call-notes", "How to log a payer call", "1. Note the reference."),
        ("appeal-letter", "A different appeal procedure", "Do something else."),
    )
    manifests = render_agent(agent(extra_skills=extra), BUNDLE, SETTINGS)
    data = by_kind(manifests, "ConfigMap")["data"]
    assert "1. Note the reference." in data["skill.payer-call-notes"]
    assert "1. Read." in data["skill.appeal-letter"]  # the bundle's own text
    pod = by_kind(manifests, "Deployment")["spec"]["template"]["spec"]
    items = next(v for v in pod["volumes"] if v["name"] == "bundle")["configMap"]["items"]
    assert {"key": "skill.payer-call-notes", "path": "skills/payer-call-notes/SKILL.md"} in items
