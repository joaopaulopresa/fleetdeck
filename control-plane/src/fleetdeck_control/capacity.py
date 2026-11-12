"""Capacity figures measured from the cluster through Prometheus."""

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

_AGENT_MEMORY = (
    'sum by (namespace) (container_memory_working_set_bytes{namespace=~"agent-.*",'
    'container="hermes"})'
)
_AGENT_CPU = (
    'sum by (namespace) (rate(container_cpu_usage_seconds_total{namespace=~"agent-.*",'
    'container="hermes"}[2m]))'
)
_NODE_MEMORY = "sum(machine_memory_bytes)"
_NODE_CORES = "sum(machine_cpu_cores)"
_NODE_MEMORY_USED = 'sum(container_memory_working_set_bytes{id="/"})'
#: Share of a node's memory given to agent pods in the estimate; the rest is for the shared
#: services and the system.
USABLE_SHARE = 0.8
ACTIVE_SHARES = (0.05, 0.1, 0.2, 0.5, 1.0)


class PrometheusClient:
    """Instant queries against Prometheus."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=5.0)

    async def query(self, expression: str) -> list[tuple[dict[str, str], float]]:
        """Return ``(labels, value)`` pairs of an instant query; empty if Prometheus is down."""
        if not self._base:
            return []
        try:
            response = await self._client.get(
                f"{self._base}/api/v1/query", params={"query": expression}
            )
            response.raise_for_status()
            result = response.json()["data"]["result"]
        except (httpx.HTTPError, KeyError, ValueError):
            log.warning("prometheus query failed: %s", expression[:60])
            return []
        return [(dict(row["metric"]), float(row["value"][1])) for row in result]

    async def scalar(self, expression: str) -> float | None:
        """Return the single value of a query, or ``None``."""
        rows = await self.query(expression)
        return rows[0][1] if rows else None


def estimate(node_memory: float, memory_per_active_agent: float) -> dict[str, Any]:
    """Return how many employees one node supports at different shares of active agents.

    A sleeping agent uses no memory, so the node only has to hold the agents awake at once.
    """
    if node_memory <= 0 or memory_per_active_agent <= 0:
        return {"always_on_agents": None, "by_active_share": []}
    awake_at_once = int(node_memory * USABLE_SHARE // memory_per_active_agent)
    return {
        "always_on_agents": awake_at_once,
        "by_active_share": [
            {"active_share": share, "employees": int(awake_at_once / share)}
            for share in ACTIVE_SHARES
        ],
        "formula": (
            f"{USABLE_SHARE:.0%} of node memory / memory per awake agent = agents "
            "awake at once; divided by the share of employees active at the same time"
        ),
    }


async def measure(prometheus: PrometheusClient, fallback_memory_bytes: float) -> dict[str, Any]:
    """Collect node size, per-agent usage and the capacity estimate."""
    memory = await prometheus.query(_AGENT_MEMORY)
    cpu = dict(
        (labels.get("namespace", ""), value) for labels, value in await prometheus.query(_AGENT_CPU)
    )
    node_memory = await prometheus.scalar(_NODE_MEMORY)
    usage = sorted(
        (
            labels.get("namespace", "").removeprefix("agent-"),
            value,
            cpu.get(labels.get("namespace", ""), 0.0),
        )
        for labels, value in memory
    )
    pods = [
        {"agent_id": agent_id, "memory_bytes": int(used), "cpu_cores": round(cores, 4)}
        for agent_id, used, cores in usage
    ]
    measured = sum(used for _, used, _ in usage) / len(usage) if usage else None
    per_agent = measured or fallback_memory_bytes
    return {
        "measured": measured is not None,
        "node": {
            "memory_bytes": int(node_memory) if node_memory else None,
            "cpu_cores": await prometheus.scalar(_NODE_CORES),
            "memory_used_bytes": await prometheus.scalar(_NODE_MEMORY_USED),
        },
        "awake_pods": pods,
        "memory_per_awake_agent_bytes": int(per_agent),
        "memory_source": (
            "average working set of the awake agent pods, from Prometheus"
            if measured
            else "the configured memory request (no pod is awake)"
        ),
        "estimate": estimate(node_memory or 0.0, per_agent),
    }
