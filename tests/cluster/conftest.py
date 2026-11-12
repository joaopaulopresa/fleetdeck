"""Fixtures for tests that run against the real k3d cluster (``make test-cluster``)."""

import json
import os
import secrets
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).parents[2]
API_URL = os.environ.get("FLEETDECK_URL", "http://127.0.0.1:8090")
KUBECONFIG = os.environ.get("FLEETDECK_KUBECONFIG", str(ROOT / ".dev-kubeconfig"))
HERMES_PYTHON = "/opt/hermes/.venv/bin/python"


def kubectl(*args: str, stdin: str | None = None, check: bool = True, timeout: int = 180) -> str:
    """Run kubectl against the demo cluster and return its stdout."""
    result = subprocess.run(
        ["kubectl", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "KUBECONFIG": KUBECONFIG},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def kubectl_json(*args: str) -> Any:
    return json.loads(kubectl(*args, "-o", "json"))


def run_in_agent(agent_id: str, script: str, *args: str) -> str:
    """Run a Python script inside the agent's Hermes container."""
    return kubectl(
        "-n",
        f"agent-{agent_id}",
        "exec",
        "-i",
        "deploy/hermes",
        "-c",
        "hermes",
        "--",
        HERMES_PYTHON,
        "-",
        *args,
        stdin=script,
    )


def can_i(verb: str, resource: str, *, service_account: str, namespace: str | None = None) -> bool:
    args = ["auth", "can-i", verb, resource, f"--as=system:serviceaccount:{service_account}"]
    if namespace:
        args += ["-n", namespace]
    return kubectl(*args, check=False).splitlines()[-1].strip() == "yes"


class Api:
    """The control-plane API as seen through the ingress."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=API_URL, timeout=200)

    def sign_in(self, person_id: str) -> dict[str, str]:
        response = self.http.post("/api/auth/login", json={"person_id": person_id})
        response.raise_for_status()
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def wake(self, agent_id: str, headers: dict[str, str]) -> float:
        started = time.monotonic()
        response = self.http.post(f"/api/agents/{agent_id}/wake", headers=headers)
        assert response.status_code == 200, response.text
        return time.monotonic() - started

    def sleep(self, agent_id: str, headers: dict[str, str]) -> float:
        started = time.monotonic()
        response = self.http.post(f"/api/agents/{agent_id}/sleep", headers=headers)
        assert response.status_code == 200, response.text
        kubectl(
            "-n",
            f"agent-{agent_id}",
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "app=hermes",
            "--timeout=120s",
        )
        return time.monotonic() - started


@pytest.fixture(scope="session")
def api() -> Iterator[Api]:
    client = Api()
    try:
        client.http.get("/api/auth/people").raise_for_status()
    except httpx.HTTPError as exc:
        pytest.exit(f"the demo cluster is not reachable at {API_URL} (run `make up`): {exc}")
    yield client
    client.http.close()


@pytest.fixture(scope="session")
def admin(api: Api) -> dict[str, str]:
    return api.sign_in("dana-whitfield")


@pytest.fixture(scope="session")
def throwaway(api: Api, admin: dict[str, str]) -> Iterator[str]:
    """A service agent created for this test session and deleted afterwards."""
    agent_id = f"it-{secrets.token_hex(3)}"
    response = api.http.post(
        "/api/agents", json={"agent_id": agent_id, "bundle": "biller"}, headers=admin
    )
    assert response.status_code == 201, response.text
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        phase = kubectl(
            "get", "agents.fleetdeck.dev", agent_id, "-o", "jsonpath={.status.phase}", check=False
        )
        if phase == "Asleep":
            break
        time.sleep(1)
    else:
        raise AssertionError(f"operator did not reconcile {agent_id}")
    yield agent_id
    api.http.delete(f"/api/agents/{agent_id}", headers=admin)
