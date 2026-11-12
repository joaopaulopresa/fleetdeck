"""Prometheus metrics of the control plane."""

from prometheus_client import Counter, Gauge, Histogram

AGENTS_BY_STATE = Gauge("fleetdeck_agents", "Agents by state", ["state"])
WAKES = Counter("fleetdeck_agent_wakes_total", "Wake-ups", ["agent", "trigger"])
WAKE_SECONDS = Histogram(
    "fleetdeck_agent_wake_seconds",
    "Time from a wake request until the agent answers its health check",
    buckets=(1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120),
)
IDLE_REAPS = Counter("fleetdeck_agent_idle_reaps_total", "Agents put to sleep for being idle")
RUNS = Counter("fleetdeck_runs_total", "Agent runs", ["trigger", "status"])
REQUESTS = Counter("fleetdeck_requests_total", "Requests between agents", ["type", "status"])
POLICY_DENIALS = Counter("fleetdeck_policy_denials_total", "Requests refused by policy", ["type"])
APPROVAL_SECONDS = Histogram(
    "fleetdeck_approval_seconds",
    "Time a reply or draft waited for its human owner",
    buckets=(5, 15, 30, 60, 120, 300, 900, 3600, 14400, 86400),
)
CONNECTOR_DENIALS = Counter(
    "fleetdeck_connector_denials_total", "Connector calls refused", ["system", "reason"]
)
