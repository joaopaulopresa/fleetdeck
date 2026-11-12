"""Typed settings of the mock corporate systems."""

from functools import lru_cache
from pathlib import Path

from fleetdeck_common.settings import CommonSettings


class MockSettings(CommonSettings):
    """Settings, read from ``FLEET_*`` environment variables."""

    seed_dir: Path = Path("/app/seed/data")
    #: Where the mutable state is kept between restarts. Empty keeps it in memory only.
    state_file: str = ""
    #: The demo's "today", so that deadlines and periods are stable in tests and recordings.
    today: str = ""


@lru_cache
def get_settings() -> MockSettings:
    """Return the process-wide settings instance."""
    return MockSettings()
