"""Typed settings of the egress proxy."""

from functools import lru_cache

from fleetdeck_common.settings import CommonSettings


class EgressSettings(CommonSettings):
    """Egress proxy settings, read from ``FLEET_*`` environment variables."""

    listen_host: str = "0.0.0.0"  # noqa: S104
    listen_port: int = 3128
    metrics_port: int = 9100
    #: Comma-separated host names. A leading dot allows every subdomain (``.example.org``).
    allowed_hosts: str = ""
    allowed_ports: str = "443"
    connect_timeout_seconds: float = 10.0

    @property
    def hosts(self) -> list[str]:
        """Return the allow-list as a list."""
        return [h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def ports(self) -> set[int]:
        """Return the allowed destination ports."""
        return {int(p) for p in self.allowed_ports.split(",") if p.strip()}


@lru_cache
def get_settings() -> EgressSettings:
    """Return the process-wide settings instance."""
    return EgressSettings()
