"""Logging setup: one line per record on stdout, the same format in every service."""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once for a service process."""
    logging.basicConfig(level=level.upper(), format=_FORMAT, stream=sys.stdout, force=True)
    for noisy in ("httpx", "httpcore", "kubernetes.client.rest"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
