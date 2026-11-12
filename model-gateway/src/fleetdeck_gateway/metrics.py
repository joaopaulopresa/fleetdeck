"""Prometheus metrics of the model gateway: usage and cost attributed to people."""

from prometheus_client import Counter, Histogram

_LABELS = ["person", "department", "model", "route", "data_class"]

CALLS = Counter("fleetdeck_model_calls_total", "Model calls", [*_LABELS, "status"])
TOKENS = Counter("fleetdeck_model_tokens_total", "Tokens", [*_LABELS, "direction"])
COST = Counter("fleetdeck_model_cost_usd_total", "Provider cost in US dollars", _LABELS)
LATENCY = Histogram(
    "fleetdeck_model_call_seconds",
    "Model call duration",
    ["route"],
    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
REFUSED = Counter(
    "fleetdeck_gateway_refused_total", "Calls refused before reaching a model", ["reason"]
)
