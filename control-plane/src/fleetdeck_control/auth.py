"""Demo sign-on and the request dependencies that identify the caller.

The demo has built-in users and no passwords: the sign-in screen lists the five fictional
employees and signs you in as the one you pick. The token format and the dependencies are the
seam where a real OIDC provider would plug in.
"""

import time
from typing import Annotated

import jwt
from fastapi import Depends, Header
from fleetdeck_common.errors import AuthenticationError, PermissionDeniedError
from fleetdeck_common.tokens import bearer_from_header, constant_time_equals

from fleetdeck_control.context import AppContext, get_context
from fleetdeck_control.models import Person

_ALGORITHM = "HS256"


def issue_session_token(person: Person, *, secret: str, ttl_seconds: int) -> str:
    """Return a signed session token for a person."""
    now = int(time.time())
    return jwt.encode(
        {"sub": person.id, "adm": person.is_admin, "iat": now, "exp": now + ttl_seconds},
        secret,
        algorithm=_ALGORITHM,
    )


async def current_person(
    context: Annotated[AppContext, Depends(get_context)],
    authorization: Annotated[str | None, Header()] = None,
) -> Person:
    """Return the signed-in person, or raise :class:`AuthenticationError`."""
    token = bearer_from_header(authorization)
    if token is None:
        raise AuthenticationError("sign in first")
    try:
        claims = jwt.decode(token, context.settings.session_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthenticationError("invalid or expired session") from exc
    async with context.db.session() as session:
        person = await session.get(Person, str(claims["sub"]))
    if person is None:
        raise AuthenticationError("unknown person")
    return person


async def current_admin(person: Annotated[Person, Depends(current_person)]) -> Person:
    """Return the signed-in person if they are an administrator."""
    if not person.is_admin:
        raise PermissionDeniedError("administrators only")
    return person


async def internal_caller(
    context: Annotated[AppContext, Depends(get_context)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Allow only platform services that present the internal token."""
    token = bearer_from_header(authorization)
    expected = context.settings.internal_token
    if not token or not expected or not constant_time_equals(token, expected):
        raise AuthenticationError("internal api: bad token")


CurrentPerson = Annotated[Person, Depends(current_person)]
CurrentAdmin = Annotated[Person, Depends(current_admin)]
Context = Annotated[AppContext, Depends(get_context)]
