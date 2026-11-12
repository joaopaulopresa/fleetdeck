"""Opaque bearer tokens: generated once, shown once, stored only as a hash."""

import hashlib
import hmac
import secrets


def new_token(prefix: str) -> str:
    """Return a fresh random token such as ``fdm_3f9a...`` (43 url-safe characters of entropy)."""
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Return the hex SHA-256 of a token. Tokens are high-entropy, so no salt is needed."""
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_from_header(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer ...`` header value."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two secrets without leaking where they differ."""
    return hmac.compare_digest(left.encode(), right.encode())
