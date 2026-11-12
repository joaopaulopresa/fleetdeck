"""Who is calling: the identity that every connector and the model gateway act on."""

from pydantic import BaseModel


class Identity(BaseModel):
    """The agent behind a token, and the person and role it works for.

    Resolved by the control plane from an opaque token. Services never trust an identity that
    arrives in a request body or in model output.
    """

    agent_id: str
    person_id: str | None
    person_name: str | None
    role: str
    department: str | None
    suspended: bool = False


class RunContext(BaseModel):
    """What an agent is working on right now, as recorded by the control plane.

    The model gateway uses it to choose the route; connectors use it to attribute their calls.
    """

    run_id: str | None = None
    session_id: str | None = None
    case_id: str | None = None
    data_class: str = "general"
    trigger: str | None = None
    trace_id: str | None = None


class Decision(BaseModel):
    """The control plane's answer to "may this agent use this tool on this record?"."""

    allowed: bool
    reason: str
    identity: Identity
    context: RunContext
