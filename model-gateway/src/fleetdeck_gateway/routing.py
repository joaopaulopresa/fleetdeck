"""Which model serves a call. Decided from the session's data class, never by the caller."""

from dataclasses import dataclass

from fleetdeck_gateway.settings import GatewaySettings


@dataclass(frozen=True)
class Route:
    """An upstream endpoint, its credential and the model to ask for."""

    name: str
    model: str
    base_url: str
    api_key: str


def choose_route(data_class: str, settings: GatewaySettings) -> Route:
    """Return the restricted route for restricted data and the default route otherwise."""
    if data_class == "restricted":
        return Route(
            name="restricted",
            model=settings.restricted_model,
            base_url=(settings.restricted_upstream_url or settings.upstream_url).rstrip("/"),
            api_key=settings.restricted_api_key or settings.openrouter_api_key,
        )
    return Route(
        name="default",
        model=settings.default_model,
        base_url=settings.upstream_url.rstrip("/"),
        api_key=settings.openrouter_api_key,
    )
