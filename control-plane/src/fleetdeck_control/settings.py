"""Typed settings of the control plane."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from fleetdeck_common.settings import CommonSettings


class Settings(CommonSettings):
    """Control-plane settings, read from ``FLEET_*`` environment variables."""

    database_url: str = "postgresql+asyncpg://fleet:fleet@localhost:5432/fleet"
    #: Signs the demo sign-on session tokens.
    session_secret: str = "change-me-in-production-0123456789"  # noqa: S105
    session_ttl_seconds: int = 12 * 3600
    #: ``incluster`` in a pod, ``kubeconfig`` on a workstation, ``fake`` in unit and API tests.
    cluster_mode: Literal["incluster", "kubeconfig", "fake"] = "incluster"
    #: An awake agent with no activity for this long is put to sleep.
    idle_seconds: int = 300
    reaper_interval_seconds: int = 15
    #: How long the activator holds a request while a pod starts.
    wake_timeout_seconds: int = 120
    wake_poll_seconds: float = 0.5
    run_timeout_seconds: int = 600
    scheduler_interval_seconds: int = 10
    background_tasks: bool = True
    #: Role bundles; a new agent gets its bundle's schedules.
    bundles_dir: Path = Path("/app/bundles")
    web_origin: str = ""
    #: Password of the row-level-security login used by the knowledge connector.
    connector_db_password: str = ""
    #: Base URL of the corporate systems, used to confirm or discard drafts.
    systems_url: str = "http://mock-systems:8000"
    #: Prefix of a Langfuse trace link; the trace id is appended. Empty when tracing is off.
    langfuse_trace_url: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
