"""Typed settings of the model gateway."""

from functools import lru_cache

from fleetdeck_common.settings import CommonSettings
from pydantic import Field


class GatewaySettings(CommonSettings):
    """Gateway settings. The provider key is read from ``OPENROUTER_API_KEY``."""

    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    upstream_url: str = "https://openrouter.ai/api/v1"
    #: The only model name agents know. The gateway decides what it means.
    model_alias: str = "fleet-default"
    default_model: str = "deepseek/deepseek-v4.1-flash-1017"
    #: Route for restricted data. In the demo an open-weight model on OpenRouter stands in for
    #: a model hosted by the company; with real patient data it must point in-house.
    restricted_model: str = "google/gemma-4-26b-a4b-it"
    restricted_upstream_url: str = ""
    restricted_api_key: str = ""
    upstream_timeout_seconds: float = 300.0
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


@lru_cache
def get_settings() -> GatewaySettings:
    """Return the process-wide settings instance."""
    return GatewaySettings()
