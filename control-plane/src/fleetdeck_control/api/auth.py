"""Sign-in endpoints."""

from fastapi import APIRouter
from fleetdeck_common.errors import NotFoundError
from sqlalchemy import select

from fleetdeck_control import audit
from fleetdeck_control.api.schemas import LoginIn, LoginOut, PersonOut
from fleetdeck_control.auth import Context, CurrentPerson, issue_session_token
from fleetdeck_control.models import Person

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/auth/people", response_model=list[PersonOut])
async def list_demo_people(context: Context) -> list[Person]:
    """List the built-in demo users for the sign-in screen."""
    async with context.db.session() as session:
        return list((await session.scalars(select(Person).order_by(Person.name))).all())


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, context: Context) -> LoginOut:
    """Sign in as one of the demo users."""
    async with context.db.session() as session:
        person = await session.get(Person, body.person_id)
        if person is None:
            raise NotFoundError("unknown person")
        await audit.record(
            session,
            kind="sign_in",
            action="sign_in",
            person_id=person.id,
            summary=f"{person.name} signed in",
        )
    token = issue_session_token(
        person,
        secret=context.settings.session_secret,
        ttl_seconds=context.settings.session_ttl_seconds,
    )
    return LoginOut(token=token, person=PersonOut.model_validate(person))


@router.get("/me", response_model=PersonOut)
async def me(person: CurrentPerson) -> Person:
    """Return the signed-in person."""
    return person
