"""Generate the Grafana dashboards.

Counts are totals since the services started.

Run after changing a panel: python build.py

The JSON files next to this script are what Grafana loads; they are committed.
"""

import json
from pathlib import Path
from typing import Any

DATASOURCE = {"type": "prometheus", "uid": "prometheus"}
# Validated categorical order (blue, orange, aqua, yellow); status colours are kept apart.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b"}


def panel(
    kind: str,
    title: str,
    queries: list[tuple[str, str]],
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    unit: str = "short",
    description: str = "",
    **options: Any,
) -> dict[str, Any]:
    targets = [
        {"datasource": DATASOURCE, "expr": expr, "legendFormat": legend, "refId": chr(65 + i)}
        for i, (expr, legend) in enumerate(queries)
    ]
    defaults: dict[str, Any] = {"unit": unit, "color": {"mode": "palette-classic"}}
    if kind == "timeseries":
        defaults["custom"] = {
            "lineWidth": 2,
            "fillOpacity": 0,
            "showPoints": "never",
            "spanNulls": True,
        }
    if kind == "stat":
        defaults["color"] = {"mode": "fixed", "fixedColor": options.pop("color", SERIES[0])}
    return {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            **(
                {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                }
                if kind == "timeseries"
                else {}
            ),
            **(
                {
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                    "colorMode": "value",
                    "graphMode": "none",
                }
                if kind == "stat"
                else {}
            ),
            **(
                {
                    "displayMode": "basic",
                    "orientation": "horizontal",
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                }
                if kind == "bargauge"
                else {}
            ),
            **options,
        },
    }


def dashboard(uid: str, title: str, panels: list[dict[str, Any]]) -> dict[str, Any]:
    for index, item in enumerate(panels, start=1):
        item["id"] = index
    return {
        "uid": uid,
        "title": title,
        "tags": ["fleetdeck"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "10s",
        "time": {"from": "now-1h", "to": "now"},
        "panels": panels,
    }


AGENT_PODS = 'namespace=~"agent-.*",container="hermes"'
WINDOW = "[$__range]"

DASHBOARDS = {
    "fleet": dashboard(
        "fleetdeck-fleet",
        "Fleetdeck: Fleet",
        [
            panel(
                "stat",
                "Awake",
                [('fleetdeck_agents{state="active"}', "")],
                0,
                0,
                4,
                4,
                color=STATUS["good"],
            ),
            panel(
                "stat",
                "Asleep",
                [('fleetdeck_agents{state="asleep"}', "")],
                4,
                0,
                4,
                4,
                color="#7a8791",
            ),
            panel(
                "stat",
                "Waking",
                [('fleetdeck_agents{state="waking"}', "")],
                8,
                0,
                4,
                4,
                color=STATUS["warning"],
            ),
            panel(
                "stat",
                "Wake-ups",
                [("sum(fleetdeck_agent_wakes_total) or vector(0)", "")],
                12,
                0,
                4,
                4,
            ),
            panel(
                "stat",
                "Idle sleeps",
                [("sum(fleetdeck_agent_idle_reaps_total) or vector(0)", "")],
                16,
                0,
                4,
                4,
            ),
            panel(
                "stat",
                "Median wake",
                [
                    (
                        "histogram_quantile(0.5, sum by (le) (rate(fleetdeck_agent_wake_seconds_bucket[1h])))",
                        "",
                    )
                ],
                20,
                0,
                4,
                4,
                unit="s",
            ),
            panel(
                "timeseries",
                "Agents by state",
                [("fleetdeck_agents", "{{state}}")],
                0,
                4,
                12,
                8,
                description="A sleeping agent has no pod.",
            ),
            panel(
                "timeseries",
                "Wake time",
                [
                    (
                        "histogram_quantile(0.5, sum by (le) (rate(fleetdeck_agent_wake_seconds_bucket[5m])))",
                        "median",
                    ),
                    (
                        "histogram_quantile(0.95, sum by (le) (rate(fleetdeck_agent_wake_seconds_bucket[5m])))",
                        "95th percentile",
                    ),
                ],
                12,
                4,
                12,
                8,
                unit="s",
                description="From the wake request until the agent answers its health check.",
            ),
            panel(
                "timeseries",
                "Memory per agent pod",
                [
                    (
                        f"sum by (namespace) (container_memory_working_set_bytes{{{AGENT_PODS}}})",
                        "{{namespace}}",
                    )
                ],
                0,
                12,
                12,
                8,
                unit="bytes",
            ),
            panel(
                "timeseries",
                "CPU per agent pod",
                [
                    (
                        f"sum by (namespace) (rate(container_cpu_usage_seconds_total{{{AGENT_PODS}}}[2m]))",
                        "{{namespace}}",
                    )
                ],
                12,
                12,
                12,
                8,
                unit="cores",
            ),
            panel(
                "bargauge",
                "Wake-ups by trigger",
                [
                    (
                        "sum by (trigger) (fleetdeck_agent_wakes_total)",
                        "{{trigger}}",
                    )
                ],
                0,
                20,
                12,
                6,
            ),
            panel(
                "stat",
                "Agent pods on the node",
                [(f"count(container_memory_working_set_bytes{{{AGENT_PODS}}})", "")],
                12,
                20,
                6,
                6,
            ),
            panel(
                "stat",
                "Node memory in use",
                [
                    (
                        'sum(container_memory_working_set_bytes{id="/"}) / sum(machine_memory_bytes)',
                        "",
                    )
                ],
                18,
                20,
                6,
                6,
                unit="percentunit",
            ),
        ],
    ),
    "usage": dashboard(
        "fleetdeck-usage",
        "Fleetdeck: Usage and cost",
        [
            panel(
                "stat",
                "Cost",
                [("sum(fleetdeck_model_cost_usd_total) or vector(0)", "")],
                0,
                0,
                6,
                4,
                unit="currencyUSD",
            ),
            panel(
                "stat",
                "Model calls",
                [("sum(fleetdeck_model_calls_total) or vector(0)", "")],
                6,
                0,
                6,
                4,
            ),
            panel(
                "stat",
                "Tokens in",
                [('sum(fleetdeck_model_tokens_total{direction="input"}) or vector(0)', "")],
                12,
                0,
                6,
                4,
            ),
            panel(
                "stat",
                "Tokens out",
                [
                    (
                        'sum(fleetdeck_model_tokens_total{direction="output"}) or vector(0)',
                        "",
                    )
                ],
                18,
                0,
                6,
                4,
            ),
            panel(
                "bargauge",
                "Cost by person",
                [
                    (
                        "sum by (person) (fleetdeck_model_cost_usd_total)",
                        "{{person}}",
                    )
                ],
                0,
                4,
                12,
                8,
                unit="currencyUSD",
            ),
            panel(
                "bargauge",
                "Cost by department",
                [
                    (
                        "sum by (department) (fleetdeck_model_cost_usd_total)",
                        "{{department}}",
                    )
                ],
                12,
                4,
                12,
                8,
                unit="currencyUSD",
            ),
            panel(
                "timeseries",
                "Tokens by model",
                [("sum by (model) (rate(fleetdeck_model_tokens_total[5m]))", "{{model}}")],
                0,
                12,
                12,
                8,
                unit="short",
                description="Tokens per second, input and output together.",
            ),
            panel(
                "bargauge",
                "Calls by data class and route",
                [
                    (
                        "sum by (data_class, route) (fleetdeck_model_calls_total)",
                        "{{data_class}} data, {{route}} route",
                    )
                ],
                12,
                12,
                12,
                8,
                description="Restricted data never uses the default route.",
            ),
            panel(
                "timeseries",
                "Model call duration",
                [
                    (
                        "histogram_quantile(0.5, sum by (le, route) (rate(fleetdeck_model_call_seconds_bucket[5m])))",
                        "median, {{route}} route",
                    )
                ],
                0,
                20,
                24,
                7,
                unit="s",
            ),
        ],
    ),
    "collaboration": dashboard(
        "fleetdeck-collaboration",
        "Fleetdeck: Collaboration",
        [
            panel(
                "stat",
                "Requests delivered",
                [('sum(fleetdeck_requests_total{status="delivered"}) or vector(0)', "")],
                0,
                0,
                6,
                4,
            ),
            panel(
                "stat",
                "Answered",
                [('sum(fleetdeck_requests_total{status="answered"}) or vector(0)', "")],
                6,
                0,
                6,
                4,
                color=STATUS["good"],
            ),
            panel(
                "stat",
                "Refused by policy",
                [("sum(fleetdeck_policy_denials_total) or vector(0)", "")],
                12,
                0,
                6,
                4,
                color=STATUS["critical"],
            ),
            panel(
                "stat",
                "Median time to approve",
                [
                    (
                        "histogram_quantile(0.5, sum by (le) (rate(fleetdeck_approval_seconds_bucket[6h])))",
                        "",
                    )
                ],
                18,
                0,
                6,
                4,
                unit="s",
            ),
            panel(
                "bargauge",
                "Requests by type",
                [
                    (
                        'sum by (type) (fleetdeck_requests_total{status=~"delivered|denied"})',
                        "{{type}}",
                    )
                ],
                0,
                4,
                12,
                8,
            ),
            panel(
                "bargauge",
                "Requests by outcome",
                [("sum by (status) (fleetdeck_requests_total)", "{{status}}")],
                12,
                4,
                12,
                8,
            ),
            panel(
                "timeseries",
                "Agent runs by what started them",
                [("sum by (trigger) (increase(fleetdeck_runs_total[5m]))", "{{trigger}}")],
                0,
                12,
                24,
                8,
            ),
        ],
    ),
    "security": dashboard(
        "fleetdeck-security",
        "Fleetdeck: Security",
        [
            panel(
                "stat",
                "Connector calls refused",
                [("sum(fleetdeck_connector_denials_total) or vector(0)", "")],
                0,
                0,
                6,
                4,
                color=STATUS["critical"],
            ),
            panel(
                "stat",
                "Egress refused",
                [('sum(fleetdeck_egress_total{outcome!="allowed"}) or vector(0)', "")],
                6,
                0,
                6,
                4,
                color=STATUS["critical"],
            ),
            panel(
                "stat",
                "Requests refused by policy",
                [("sum(fleetdeck_policy_denials_total) or vector(0)", "")],
                12,
                0,
                6,
                4,
                color=STATUS["critical"],
            ),
            panel(
                "stat",
                "Gateway refusals",
                [("sum(fleetdeck_gateway_refused_total) or vector(0)", "")],
                18,
                0,
                6,
                4,
                color=STATUS["critical"],
                description="Calls with a missing, unknown or suspended virtual key.",
            ),
            panel(
                "bargauge",
                "Refused connector calls by system",
                [
                    (
                        "sum by (system, reason) (fleetdeck_connector_denials_total)",
                        "{{system}}: {{reason}}",
                    )
                ],
                0,
                4,
                12,
                8,
                description="role: the role has no such tool. case: not a member of the record's case.",
            ),
            panel(
                "bargauge",
                "Egress attempts",
                [("sum by (outcome) (fleetdeck_egress_total)", "{{outcome}}")],
                12,
                4,
                12,
                8,
            ),
            panel(
                "timeseries",
                "Refusals over time",
                [
                    ("sum(increase(fleetdeck_connector_denials_total[5m]))", "connector"),
                    ("sum(increase(fleetdeck_policy_denials_total[5m]))", "request policy"),
                    ('sum(increase(fleetdeck_egress_total{outcome!="allowed"}[5m]))', "egress"),
                ],
                0,
                12,
                24,
                8,
            ),
        ],
    ),
}

if __name__ == "__main__":
    for name, content in DASHBOARDS.items():
        Path(__file__).with_name(f"{name}.json").write_text(json.dumps(content, indent=1) + "\n")
