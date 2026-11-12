"""The HTTP API of the mock corporate systems: one router per system."""

import re
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from fleetdeck_common import errors
from fleetdeck_common.logs import configure_logging
from fleetdeck_common.tokens import bearer_from_header, constant_time_equals
from pydantic import BaseModel, Field

from fleetdeck_mock import rules
from fleetdeck_mock.settings import MockSettings, get_settings
from fleetdeck_mock.store import Record, Store


def get_store(request: Request) -> Store:
    """Return the process-wide store."""
    store: Store = request.app.state.store
    return store


def platform_only(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    """Allow only platform services: connectors and the control plane."""
    token = bearer_from_header(authorization)
    expected: str = request.app.state.settings.internal_token
    if not token or not expected or not constant_time_equals(token, expected):
        raise errors.AuthenticationError("corporate systems accept platform services only")


Data = Annotated[Store, Depends(get_store)]


class Authored(BaseModel):
    """Who prepared a draft: the person the agent works for."""

    author: str
    case_id: str | None = None


# --- Patient record ---------------------------------------------------------------------

patient_record = APIRouter(prefix="/patient-record", tags=["patient record"])


class NoteDraft(Authored):
    """A clinical note prepared for the physician's signature."""

    patient_id: str
    title: str = Field(max_length=200)
    text: str


def _brief_encounter(encounter: Record) -> Record:
    return {key: encounter[key] for key in ("id", "date", "type", "clinician", "summary")}


@patient_record.get("/patients")
def list_patients(
    store: Data, case_id: Annotated[list[str] | None, Query()] = None
) -> list[Record]:
    """List patients, optionally only those whose episode is one of the given cases."""
    wanted = set(case_id or [])
    return [
        {key: p[key] for key in ("id", "mrn", "name", "date_of_birth", "case_id")}
        for p in store.all("patients")
        if case_id is None or p["case_id"] in wanted
    ]


@patient_record.get("/patients/{patient_id}")
def patient_summary(patient_id: str, store: Data) -> Record:
    """Return the chart summary: demographics, problems, medications and the encounter list."""
    patient = store.get("patients", patient_id)
    encounters = [e for e in store.all("encounters") if e["patient_id"] == patient_id]
    encounters.sort(key=lambda e: e["date"], reverse=True)
    return {**patient, "encounters": [_brief_encounter(e) for e in encounters]}


@patient_record.get("/encounters/{encounter_id}")
def encounter(encounter_id: str, store: Data) -> Record:
    """Return one encounter with its full note and results."""
    record = store.get("encounters", encounter_id)
    patient = store.get("patients", record["patient_id"])
    return {**record, "case_id": patient["case_id"]}


@patient_record.post("/drafts/note")
def draft_note(body: NoteDraft, store: Data) -> Record:
    """Store an unsigned clinical note."""
    patient = store.get("patients", body.patient_id)
    return store.new_draft(
        "patient-record",
        "clinical_note",
        author=body.author,
        case_id=patient["case_id"],
        title=f"Clinical note for {patient['name']}: {body.title}",
        payload={"patient_id": body.patient_id, "title": body.title, "text": body.text},
    )


# --- Billing and claims -----------------------------------------------------------------

billing = APIRouter(prefix="/billing", tags=["billing"])


class AppealDraft(Authored):
    """An appeal prepared for the biller to file."""

    claim_id: str
    justification: str


class AppealDecision(BaseModel):
    """The payer's answer to an appeal. In the demo the payer is simulated."""

    claim_id: str
    outcome: Annotated[str, "upheld | overturned"] = "upheld"
    reason: str = "The payer maintains that the service was not medically necessary."


def _claim_view(claim: Record, store: Store) -> Record:
    payer = store.get("payers", claim["payer_id"])
    view = {key: value for key, value in claim.items() if key != "billed_cents"}
    return {
        **view,
        "billed_amount": rules.dollars(int(claim["billed_cents"])),
        "payer": payer["name"],
        "contract_id": payer["contract_id"],
    }


@billing.get("/claims")
def list_claims(store: Data, status: Annotated[list[str] | None, Query()] = None) -> list[Record]:
    """List claims, optionally filtered by status."""
    return [
        {
            "id": c["id"],
            "patient_name": c["patient_name"],
            "status": c["status"],
            "service_date": c["service_date"],
            "procedure": c["procedure"],
            "billed_amount": rules.dollars(int(c["billed_cents"])),
            "denial_code": (c.get("denial") or {}).get("code"),
        }
        for c in store.all("claims")
        if status is None or c["status"] in status
    ]


@billing.get("/claims/{claim_id}")
def claim(claim_id: str, store: Data) -> Record:
    """Return one claim with its denial and appeal history."""
    return _claim_view(store.get("claims", claim_id), store)


@billing.get("/denial-summary")
def get_denial_summary(store: Data) -> Record:
    """Return the unresolved denials by stage, computed in code."""
    return rules.denial_summary(store.all("claims"))


@billing.post("/drafts/appeal")
def draft_appeal(body: AppealDraft, store: Data) -> Record:
    """Store an appeal that is not filed yet."""
    record = store.get("claims", body.claim_id)
    if record["status"] not in ("denied", "appeal_denied"):
        raise errors.ConflictError(
            f"claim {body.claim_id} is {record['status']}: nothing to appeal"
        )
    level = len(record.get("appeals") or []) + 1
    return store.new_draft(
        "billing",
        "appeal",
        author=body.author,
        case_id=record.get("case_id"),
        title=f"Level {level} appeal for claim {record['id']} ({record['patient_name']})",
        payload={"claim_id": record["id"], "level": level, "justification": body.justification},
    )


@billing.post("/simulate/appeal-decision")
def simulate_appeal_decision(body: AppealDecision, store: Data) -> Record:
    """Record the payer's decision on the open appeal of a claim."""
    record = store.get("claims", body.claim_id)
    if record["status"] != "appealed":
        raise errors.ConflictError(f"claim {body.claim_id} has no open appeal")
    appeal = record["appeals"][-1]
    appeal.update(
        outcome=body.outcome, outcome_date=store.today().isoformat(), outcome_reason=body.reason
    )
    record["status"] = "appeal_denied" if body.outcome == "upheld" else "paid"
    store.save()
    return _claim_view(record, store)


# --- ERP and invoices -------------------------------------------------------------------

erp = APIRouter(prefix="/erp", tags=["erp"])


class DisputeFlagDraft(Authored):
    """A change of a revenue entry to disputed, prepared for the tax analyst."""

    claim_id: str
    reason: str


def _entry_view(entry: Record) -> Record:
    view = {key: value for key, value in entry.items() if key != "amount_cents"}
    return {**view, "amount": rules.dollars(int(entry["amount_cents"]))}


def _entry_for(claim_id: str, store: Store) -> Record:
    for entry in store.all("revenue_entries"):
        if entry["claim_id"] == claim_id:
            return entry
    raise errors.NotFoundError(f"no revenue entry for claim {claim_id}")


@erp.get("/revenue/{claim_id}")
def revenue_entry(claim_id: str, store: Data) -> Record:
    """Return the revenue entry booked for a claim."""
    return _entry_view(_entry_for(claim_id, store))


@erp.post("/drafts/flag-disputed")
def draft_flag_disputed(body: DisputeFlagDraft, store: Data) -> Record:
    """Prepare marking a claim's revenue as disputed. Nothing is posted yet."""
    entry = _entry_for(body.claim_id, store)
    if entry["status"] == "disputed":
        raise errors.ConflictError(f"revenue of claim {body.claim_id} is already disputed")
    return store.new_draft(
        "erp",
        "flag_disputed",
        author=body.author,
        case_id=entry.get("case_id"),
        title=f"Mark revenue of claim {body.claim_id} "
        f"({rules.dollars(int(entry['amount_cents']))}) as disputed",
        payload={"claim_id": body.claim_id, "reason": body.reason},
    )


@erp.get("/tax-engine")
def tax_engine(period: str, store: Data) -> Record:
    """Run the tax engine for a period (``YYYY-MM``)."""
    return rules.run_tax_engine(period, store.all("revenue_entries"))


@erp.get("/month-end")
def month_end(period: str, store: Data) -> Record:
    """Return the month-end close status of a period: only the exceptions need a person."""
    entries = [e for e in store.all("revenue_entries") if e["period"] == period]
    disputed = [e for e in entries if e["status"] == "disputed"]
    return {
        "period": period,
        "entries": len(entries),
        "posted_without_exception": len(entries) - len(disputed),
        "exceptions": [
            {
                "claim_id": e["claim_id"],
                "amount": rules.dollars(int(e["amount_cents"])),
                "reason": e.get("disputed_reason", ""),
            }
            for e in disputed
        ],
        "source": "ERP revenue ledger",
    }


# --- Docket -----------------------------------------------------------------------------

docket = APIRouter(prefix="/docket", tags=["docket"])


class OpenCaseDraft(Authored):
    """A legal matter prepared for the lawyer to open."""

    claim_id: str
    title: str = Field(max_length=200)
    counterparty: str


class NoticeDraft(Authored):
    """A formal notice prepared for the lawyer to send."""

    legal_case_id: str
    text: str


@docket.get("/rules")
def deadline_rules() -> list[Record]:
    """List the deadline rules the docket knows."""
    return [
        {
            "rule": r.key,
            "title": r.title,
            "days": r.days,
            "counting": r.counting,
            "source": r.source,
        }
        for r in rules.DEADLINE_RULES.values()
    ]


@docket.get("/deadline")
def deadline(rule: str, event_date: date) -> Record:
    """Compute a due date from a rule and an event date."""
    try:
        return rules.compute_deadline(rule, event_date)
    except KeyError as exc:
        raise errors.NotFoundError(f"unknown deadline rule {rule!r}") from exc


@docket.get("/cases")
def legal_cases(store: Data, case_id: Annotated[list[str] | None, Query()] = None) -> list[Record]:
    """List legal matters, optionally only those of the given case spaces."""
    wanted = set(case_id or [])
    return [c for c in store.all("legal_cases") if case_id is None or c["case_id"] in wanted]


@docket.get("/cases/{legal_case_id}")
def legal_case(legal_case_id: str, store: Data) -> Record:
    """Return one legal matter with its deadlines."""
    record = store.get("legal_cases", legal_case_id)
    deadlines = [d for d in store.all("deadlines") if d["legal_case_id"] == legal_case_id]
    return {**record, "deadlines": sorted(deadlines, key=lambda d: d["due_date"])}


@docket.get("/deadlines")
def deadlines(store: Data, case_id: Annotated[list[str] | None, Query()] = None) -> list[Record]:
    """List open deadlines, soonest first."""
    wanted = set(case_id or [])
    cases = {c["id"]: c for c in store.all("legal_cases")}
    rows = [
        {**d, "matter": cases[d["legal_case_id"]]["title"]}
        for d in store.all("deadlines")
        if d["status"] == "open"
        and (case_id is None or cases[d["legal_case_id"]]["case_id"] in wanted)
    ]
    return sorted(rows, key=lambda d: d["due_date"])


@docket.get("/summary")
def docket_summary(store: Data) -> Record:
    """Return the open legal matters with their next deadline."""
    matters = []
    for record in store.all("legal_cases"):
        if record["status"] != "open":
            continue
        pending = sorted(
            d["due_date"]
            for d in store.all("deadlines")
            if d["legal_case_id"] == record["id"] and d["status"] == "open"
        )
        matters.append(
            {
                "id": record["id"],
                "title": record["title"],
                "stage": record["stage"],
                "claim_id": record.get("claim_id"),
                "next_deadline": pending[0] if pending else None,
            }
        )
    return {"open_matters": len(matters), "matters": matters, "source": "docket system"}


@docket.post("/drafts/open-case")
def draft_open_case(body: OpenCaseDraft, store: Data) -> Record:
    """Prepare opening a legal matter for a claim."""
    record = store.get("claims", body.claim_id)
    if any(c.get("claim_id") == body.claim_id for c in store.all("legal_cases")):
        raise errors.ConflictError(f"a legal matter for claim {body.claim_id} already exists")
    return store.new_draft(
        "docket",
        "open_case",
        author=body.author,
        case_id=record.get("case_id"),
        title=f"Open legal matter: {body.title}",
        payload={"claim_id": body.claim_id, "title": body.title, "counterparty": body.counterparty},
    )


@docket.post("/drafts/formal-notice")
def draft_formal_notice(body: NoticeDraft, store: Data) -> Record:
    """Prepare a formal notice of dispute. Its response deadline is computed when it is sent."""
    record = store.get("legal_cases", body.legal_case_id)
    return store.new_draft(
        "docket",
        "formal_notice",
        author=body.author,
        case_id=record["case_id"],
        title=f"Formal notice of dispute to {record['counterparty']}",
        payload={"legal_case_id": record["id"], "text": body.text},
    )


# --- Contracts --------------------------------------------------------------------------

contracts = APIRouter(prefix="/contracts", tags=["contracts"])
_WORD = re.compile(r"[a-z0-9]+")


@contracts.get("")
def search_contracts(store: Data, query: str = "") -> list[Record]:
    """Search contracts and clauses by words; the best matching clauses come first."""
    words = set(_WORD.findall(query.lower()))
    hits: list[tuple[int, Record]] = []
    for contract in store.all("contracts"):
        header = f"{contract['counterparty']} {contract['title']} {contract['kind']}".lower()
        for clause in contract["clauses"]:
            text = f"{header} {clause['title']} {clause['text']}".lower()
            score = sum(text.count(word) for word in words) if words else 1
            if score:
                hits.append(
                    (
                        score,
                        {
                            "contract_id": contract["id"],
                            "counterparty": contract["counterparty"],
                            "clause_id": clause["id"],
                            "clause_title": clause["title"],
                            "excerpt": clause["text"][:240],
                        },
                    )
                )
    hits.sort(key=lambda hit: hit[0], reverse=True)
    return [hit for _, hit in hits[:8]]


@contracts.get("/{contract_id}")
def contract(contract_id: str, store: Data) -> Record:
    """Return a contract with the list of its clauses."""
    record = store.get("contracts", contract_id)
    return {
        **{k: v for k, v in record.items() if k != "clauses"},
        "clauses": [{"id": c["id"], "title": c["title"]} for c in record["clauses"]],
    }


@contracts.get("/{contract_id}/clauses/{clause_id}")
def clause(contract_id: str, clause_id: str, store: Data) -> Record:
    """Return the full text of one clause."""
    record = store.get("contracts", contract_id)
    for item in record["clauses"]:
        if item["id"] == clause_id:
            return {"contract_id": contract_id, "counterparty": record["counterparty"], **item}
    raise errors.NotFoundError(f"contract {contract_id} has no clause {clause_id}")


# --- Drafts: confirmed or discarded by their human owner, through the control plane ------

drafts = APIRouter(tags=["drafts"])


class Confirmation(BaseModel):
    """Who confirmed, and the final text if the person edited the draft."""

    person_id: str
    body: str = ""


def _apply(draft: Record, confirmation: Confirmation, store: Store) -> Record:
    payload, today = draft["payload"], store.today().isoformat()
    text = confirmation.body.strip()
    kind = draft["kind"]
    if kind == "clinical_note":
        return store.add(
            "encounters",
            {
                "id": f"ENC-{draft['id'][3:]}",
                "patient_id": payload["patient_id"],
                "date": today,
                "type": "Clinical note",
                "clinician": confirmation.person_id,
                "summary": payload["title"],
                "note": text or payload["text"],
                "results": [],
                "signed_by": confirmation.person_id,
                "signed_on": today,
            },
        )
    if kind == "appeal":
        record = store.get("claims", payload["claim_id"])
        record.setdefault("appeals", []).append(
            {
                "level": payload["level"],
                "filed_date": today,
                "filed_by": confirmation.person_id,
                "justification": text or payload["justification"],
                "outcome": None,
            }
        )
        record["status"] = "appealed"
        return record
    if kind == "flag_disputed":
        entry = _entry_for(payload["claim_id"], store)
        entry.update(
            status="disputed",
            disputed_reason=text or payload["reason"],
            disputed_on=today,
            disputed_by=confirmation.person_id,
        )
        return entry
    if kind == "open_case":
        claim_record = store.get("claims", payload["claim_id"])
        claim_record["status"] = "in_dispute"
        return store.add(
            "legal_cases",
            {
                "id": f"LM-{draft['id'][3:9]}",
                "title": payload["title"],
                "claim_id": payload["claim_id"],
                "counterparty": payload["counterparty"],
                "case_id": draft["case_id"],
                "status": "open",
                "stage": "Notice of dispute pending",
                "opened_on": today,
                "opened_by": confirmation.person_id,
                "notices": [],
            },
        )
    if kind == "formal_notice":
        matter = store.get("legal_cases", payload["legal_case_id"])
        matter["notices"].append(
            {"sent_on": today, "sent_by": confirmation.person_id, "text": text or payload["text"]}
        )
        matter["stage"] = "Notice of dispute sent, waiting for the payer"
        due = rules.compute_deadline("notice_response", store.today())
        store.add(
            "deadlines",
            {
                "id": f"DL-{draft['id'][3:9]}",
                "legal_case_id": matter["id"],
                "status": "open",
                "rule": due["rule"],
                "title": due["title"],
                "event_date": due["event_date"],
                "due_date": due["due_date"],
                "computation": due["computation"],
                "source": due["source"],
            },
        )
        return matter
    raise errors.ConflictError(f"unknown draft kind {kind!r}")


@drafts.get("/{system}/drafts/{draft_id}")
def get_draft(system: str, draft_id: str, store: Data) -> Record:
    """Return a draft."""
    draft = store.drafts.get(draft_id)
    if draft is None or draft["system"] != system:
        raise errors.NotFoundError(f"{system}: draft {draft_id} not found")
    return draft


@drafts.post("/{system}/drafts/{draft_id}/confirm")
def confirm_draft(system: str, draft_id: str, body: Confirmation, store: Data) -> Record:
    """Make a draft real: sign the note, file the appeal, post the entry, send the notice."""
    draft = store.take_draft(system, draft_id)
    result = _apply(draft, body, store)
    draft.update(status="confirmed", confirmed_by=body.person_id)
    store.save()
    return {"draft": draft_id, "status": "confirmed", "result": result}


@drafts.post("/{system}/drafts/{draft_id}/discard")
def discard_draft(system: str, draft_id: str, body: Confirmation, store: Data) -> Record:
    """Throw a draft away."""
    draft = store.take_draft(system, draft_id)
    draft.update(status="discarded", discarded_by=body.person_id)
    store.save()
    return {"draft": draft_id, "status": "discarded"}


admin = APIRouter(prefix="/admin", tags=["admin"])


@admin.post("/reset")
def reset(store: Data) -> dict[str, str]:
    """Reload the seed and drop every change and draft (used to restart the demo)."""
    store.reset()
    return {"status": "reset"}


def create_app(settings: MockSettings | None = None, store: Store | None = None) -> FastAPI:
    """Build the application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Meridian Health Group systems (synthetic)", version="0.1.0")
    app.state.settings = settings
    app.state.store = store or Store(settings.seed_dir, settings.state_file, settings.today)

    statuses: dict[type[errors.FleetdeckError], int] = {
        errors.NotFoundError: 404,
        errors.ConflictError: 409,
        errors.AuthenticationError: 401,
    }

    @app.exception_handler(errors.FleetdeckError)
    async def fleetdeck_error(_: Request, exc: errors.FleetdeckError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=statuses.get(type(exc), 400))

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "today": app.state.store.today().isoformat()}

    for router in (patient_record, billing, erp, docket, contracts, drafts, admin):
        app.include_router(router, dependencies=[Depends(platform_only)])
    return app
