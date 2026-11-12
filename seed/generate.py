"""Generate the synthetic dataset of Meridian Health Group.

Run once and commit the result; nothing is generated at run time:

    uv run python seed/generate.py

The storyline (``seed/storyline.yaml``) is written by hand. A small, cheap model (``SEED_MODEL``
through OpenRouter) invents the bulk: patients, encounter notes and contract clauses. It is asked
for small batches, every batch is validated against a schema and retried when invalid. Money,
dates, statuses and identifiers are produced by code with a fixed random seed, never by the model.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).parent
OUT = ROOT / "data"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
RNG = random.Random(20261112)  # noqa: S311  (reproducible demo data, not security)

PATIENTS = 24
BATCH = 6
OTHER_PAYERS = [
    "Lakeshore Benefit Trust",
    "Cedar Valley Health Plan",
    "Harborline Insurance Company",
]
SUPPLIERS = ["Brightpath Medical Supply", "Corvane Imaging Services"]
PROCEDURES = [
    ("99214", "Office visit, established patient, moderate complexity", 21_500),
    ("93306", "Transthoracic echocardiogram with Doppler", 142_000),
    ("93015", "Cardiovascular stress test", 61_000),
    ("75574", "CT angiography of the coronary arteries", 238_000),
    ("93458", "Left heart catheterization with coronary angiography", 1_184_000),
    ("92928", "Percutaneous coronary stent placement", 2_960_000),
    ("33208", "Dual-chamber pacemaker implantation", 3_415_000),
    ("70553", "MRI of the brain with and without contrast", 312_000),
    ("45378", "Diagnostic colonoscopy", 187_500),
    ("27447", "Total knee arthroplasty", 4_230_000),
]
DENIALS = [
    ("CO-50", "Not deemed a medical necessity by the payer."),
    ("CO-197", "Precertification or authorization absent."),
    ("CO-16", "Claim lacks information needed for adjudication."),
    ("CO-97", "Payment is included in the allowance for another service."),
]


class Patient(BaseModel):
    """A generated patient."""

    name: str = Field(min_length=5, max_length=60)
    date_of_birth: date
    sex: str = Field(pattern="^(female|male)$")
    problems: list[str] = Field(min_length=1, max_length=4)
    medications: list[str] = Field(min_length=1, max_length=5)
    allergies: list[str] = Field(max_length=3)


class PatientBatch(BaseModel):
    """One batch of patients."""

    patients: list[Patient] = Field(min_length=BATCH, max_length=BATCH)


class Note(BaseModel):
    """A generated encounter note."""

    type: str = Field(min_length=4, max_length=60)
    summary: str = Field(min_length=10, max_length=140)
    note: str = Field(min_length=120, max_length=900)


class NoteBatch(BaseModel):
    """One note per patient of the batch, in order."""

    notes: list[Note]


class Clause(BaseModel):
    """A generated contract clause."""

    title: str = Field(min_length=4, max_length=80)
    text: str = Field(min_length=120, max_length=900)


class ClauseBatch(BaseModel):
    """Extra clauses for one contract."""

    clauses: list[Clause] = Field(min_length=2, max_length=2)


def ask[T: BaseModel](schema: type[T], prompt: str, *, check: Any = None, attempts: int = 4) -> T:
    """Ask the seed model for JSON matching ``schema``; retry on invalid output."""
    model = os.environ["SEED_MODEL"]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    body = {
        "model": model,
        "temperature": 0.9,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You write synthetic demo data. Everything is "
                "fictional. Reply with one JSON object and nothing else.",
            },
            {
                "role": "user",
                "content": f"{prompt}\n\nJSON schema:\n{json.dumps(schema.model_json_schema())}",
            },
        ],
    }
    for attempt in range(1, attempts + 1):
        response = httpx.post(OPENROUTER, headers=headers, json=body, timeout=120)
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        try:
            parsed = schema.model_validate_json(text[text.index("{") : text.rindex("}") + 1])
            if check is not None:
                check(parsed)
            return parsed
        except (ValidationError, ValueError) as error:
            print(f"  invalid batch (attempt {attempt}): {str(error)[:160]}", file=sys.stderr)
    raise SystemExit(f"the seed model did not return a valid {schema.__name__}")


def generate_patients() -> list[Patient]:
    """Return ``PATIENTS`` distinct patients."""
    patients: list[Patient] = []
    seen = {"Harold Whitaker"}
    while len(patients) < PATIENTS:

        def distinct(batch: PatientBatch) -> None:
            names = [p.name for p in batch.patients]
            if len(set(names)) != len(names) or seen & set(names):
                raise ValueError("duplicate names")
            for patient in batch.patients:
                if not date(1938, 1, 1) <= patient.date_of_birth <= date(2004, 12, 31):
                    raise ValueError("date of birth out of range")

        batch = ask(
            PatientBatch,
            (
                f"Invent {BATCH} fictional adult patients of a US clinic network that sees cardiology, "
                "internal medicine and orthopedics. Varied first and last names from different "
                "backgrounds; no celebrities. Each has 1-3 chronic problems, 1-4 medications with "
                f"dose, and 0-2 allergies. Do not use these names: {sorted(seen)}."
            ),
            check=distinct,
        )
        patients.extend(batch.patients)
        seen.update(p.name for p in batch.patients)
        print(f"patients: {len(patients)}/{PATIENTS}")
    return patients[:PATIENTS]


def generate_notes(patients: list[Patient]) -> list[Note]:
    """Return one encounter note per patient."""
    notes: list[Note] = []
    for start in range(0, len(patients), BATCH):
        group = patients[start : start + BATCH]

        def right_size(batch: NoteBatch, expected: int = len(group)) -> None:
            if len(batch.notes) != expected:
                raise ValueError(f"expected {expected} notes")

        listing = "\n".join(
            f"{i + 1}. {p.sex}, born {p.date_of_birth.year}, problems: {', '.join(p.problems)}"
            for i, p in enumerate(group)
        )
        batch = ask(
            NoteBatch,
            (
                f"Write one outpatient encounter note for each of these {len(group)} patients, in the "
                "same order. Each: the visit type, a one-line summary, and a 50-90 word clinical note "
                "in the terse style of a physician (findings, assessment, plan). Do not mention the "
                f"patient's name.\n{listing}"
            ),
            check=right_size,
        )
        notes.extend(batch.notes)
        print(f"notes: {len(notes)}/{len(patients)}")
    return notes


def generate_clauses(counterparty: str, kind: str) -> list[Clause]:
    """Return two extra clauses for a contract."""
    topics = (
        "claims submission and audit rights"
        if kind == "payer"
        else "delivery, service levels and price adjustment"
    )
    return ask(
        ClauseBatch,
        (
            f"Write two clauses of a {kind} agreement between Meridian Health Group (a clinic "
            f"network) and {counterparty}, about {topics}. Formal contract English, 60-110 words "
            "each, with a short title. No clause numbers in the text."
        ),
    ).clauses


def money_free(record: dict[str, Any]) -> dict[str, Any]:
    """Return the record ready for JSON."""
    return json.loads(json.dumps(record, default=str))


def build() -> None:
    """Generate every file under ``seed/data`` and ``seed/cases.yaml``."""
    story = yaml.safe_load((ROOT / "storyline.yaml").read_text())
    today = date.fromisoformat(story["today"])
    physicians = story["physicians"]
    generated = generate_patients()
    notes = generate_notes(generated)

    # --- Patient record -------------------------------------------------------------
    patients = [{**story["patient"], "case_id": "ep-pat-0001"}]
    encounters = [
        {**e, "patient_id": "PAT-0001", "clinician": "Dr. Elena Park"} for e in story["encounters"]
    ]
    for index, (patient, note) in enumerate(zip(generated, notes, strict=True), start=2):
        # Dr. Park treats every third generated patient; the others belong to colleagues
        # who have no agent in the demo.
        physician = physicians[0] if index % 3 == 2 else physicians[1 + index % 2]
        patient_id = f"PAT-{index:04d}"
        patients.append(
            {
                "id": patient_id,
                "mrn": f"MHG-{RNG.randint(100000, 199999)}",
                **patient.model_dump(mode="json"),
                "physician_id": physician["id"],
                "physician": physician["name"],
                "case_id": f"ep-{patient_id.lower()}",
            }
        )
        encounters.append(
            {
                "id": f"ENC-{index:04d}01",
                "patient_id": patient_id,
                "date": (today - timedelta(days=RNG.randint(20, 240))).isoformat(),
                "clinician": physician["name"],
                **note.model_dump(),
                "results": [],
            }
        )

    # --- Contracts and payers -------------------------------------------------------
    northwind = story["contract"]
    payers = [story["payer"]]
    contracts = [northwind]
    for name in OTHER_PAYERS:
        slug = name.split()[0].upper()
        clauses = [{**c, "text": c["text"]} for c in northwind["clauses"]]
        extra = generate_clauses(name, "payer")
        clauses += [{"id": f"{14 + i}.1", **c.model_dump()} for i, c in enumerate(extra)]
        contracts.append(
            {
                "id": f"CTR-{slug}-2025",
                "kind": "payer",
                "counterparty": name,
                "title": "Provider Participation Agreement",
                "effective": "2025-01-01",
                "clauses": clauses,
            }
        )
        payers.append({"id": f"PAY-{slug}", "name": name, "contract_id": f"CTR-{slug}-2025"})
        print(f"contract: {name}")
    for name in SUPPLIERS:
        slug = name.split()[0].upper()
        extra = generate_clauses(name, "supplier")
        contracts.append(
            {
                "id": f"CTR-{slug}-2026",
                "kind": "supplier",
                "counterparty": name,
                "title": "Supply and Services Agreement",
                "effective": "2026-01-01",
                "clauses": [{"id": f"{3 + i}.1", **c.model_dump()} for i, c in enumerate(extra)],
            }
        )
        print(f"contract: {name}")

    # --- Claims and revenue ---------------------------------------------------------
    existing = story["existing_dispute"]
    claims = [story["claim"]]
    number = 600
    for patient in patients[1:]:
        for _ in range(RNG.randint(1, 2)):
            number += RNG.randint(3, 40)
            cpt, procedure, cents = RNG.choice(PROCEDURES)
            service = today - timedelta(days=RNG.randint(25, 150))
            claims.append(
                {
                    "id": f"CLM-2026-{number:05d}",
                    "patient_id": patient["id"],
                    "patient_name": patient["name"],
                    "member_id": f"{RNG.choice('NLCH')}{RNG.choice('WBVI')}-"
                    f"{RNG.randint(10, 99)}-{RNG.randint(100000, 999999)}",
                    "payer_id": RNG.choice(payers)["id"],
                    "physician_id": patient["physician_id"],
                    "physician": patient["physician"],
                    "procedure": procedure,
                    "cpt": cpt,
                    "service_date": service.isoformat(),
                    "billed_cents": cents,
                    "status": "paid",
                    "denial": None,
                    "appeals": [],
                    "case_id": None,
                }
            )

    def deny(claim: dict[str, Any], status: str) -> None:
        code, reason = RNG.choice(DENIALS)
        denied = date.fromisoformat(claim["service_date"]) + timedelta(days=RNG.randint(10, 18))
        claim.update(
            status=status, denial={"code": code, "reason": reason, "date": denied.isoformat()}
        )
        if status != "denied":
            filed = denied + timedelta(days=RNG.randint(5, 20))
            outcome = None if status == "appealed" else "upheld"
            claim["appeals"] = [
                {
                    "level": 1,
                    "filed_date": filed.isoformat(),
                    "filed_by": "marcus-reyes",
                    "justification": "Appeal filed with supporting documentation.",
                    "outcome": outcome,
                    "outcome_date": (filed + timedelta(days=21)).isoformat() if outcome else None,
                }
            ]

    others = claims[1:]
    RNG.shuffle(others)
    for claim, status in zip(
        others, ["denied"] * 3 + ["appealed"] * 3 + ["appeal_denied"], strict=False
    ):
        deny(claim, status)
    disputed = max((c for c in others if c["status"] == "paid"), key=lambda c: c["billed_cents"])
    lakeshore = next(p for p in payers if p["name"] == existing["legal_case"]["counterparty"])
    disputed.update(
        id=existing["claim_id"],
        payer_id=lakeshore["id"],
        cpt="22612",
        procedure="Lumbar spinal fusion",
        billed_cents=6_120_000,
        service_date="2026-07-21",
        case_id=existing["case_id"],
    )
    deny(disputed, "in_dispute")
    disputed["denial"] = {"code": "CO-50", "reason": DENIALS[0][1], "date": "2026-08-04"}
    disputed["appeals"][0].update(filed_date="2026-08-18", outcome_date="2026-09-11")
    claims.sort(key=lambda c: c["id"])

    revenue = [
        {
            "id": f"REV-{c['id'][4:]}",
            "claim_id": c["id"],
            "period": c["service_date"][:7],
            "amount_cents": c["billed_cents"],
            "case_id": c["case_id"],
            "status": "disputed" if c["status"] == "in_dispute" else "recognized",
            **(
                {
                    "disputed_reason": f"Formal dispute {existing['legal_case']['id']}",
                    "disputed_on": "2026-10-05",
                    "disputed_by": "tom-becker",
                }
                if c["status"] == "in_dispute"
                else {}
            ),
        }
        for c in claims
    ]

    # --- Docket ---------------------------------------------------------------------
    matter = existing["legal_case"]
    sent = date.fromisoformat(matter.pop("notice_sent_on"))
    sys.path.insert(0, str(ROOT.parent / "mock-systems" / "src"))
    from fleetdeck_mock.rules import compute_deadline

    due = compute_deadline("notice_response", sent)
    legal_cases = [
        {
            **matter,
            "claim_id": existing["claim_id"],
            "case_id": existing["case_id"],
            "notices": [
                {
                    "sent_on": sent.isoformat(),
                    "sent_by": "priya-nair",
                    "text": "Notice of dispute under clause 12.2.",
                }
            ],
        }
    ]
    deadlines = [
        {
            "id": "DL-2026-031",
            "legal_case_id": matter["id"],
            "status": "open",
            "rule": due["rule"],
            "title": due["title"],
            "event_date": due["event_date"],
            "due_date": due["due_date"],
            "computation": due["computation"],
            "source": due["source"],
        }
    ]

    # --- Case spaces ----------------------------------------------------------------
    cases = [
        {
            "id": p["case_id"],
            "kind": "patient_episode",
            "title": f"Care of {p['name']}",
            "summary": f"Treating physician: {p['physician']}",
            "data_class": "restricted",
            "members": {p["physician_id"]: "owner"} if p["physician_id"] == "elena-park" else {},
        }
        for p in patients
    ]
    story_claim = story["claim"]
    cases.append(
        {
            "id": story_claim["case_id"],
            "kind": "claim_dispute",
            "title": f"Denied claim {story_claim['id']} (Northwind Mutual Insurance)",
            "summary": "CRT-D implantation denied as not medically necessary.",
            "data_class": "confidential",
            "members": {"marcus-reyes": "owner"},
        }
    )
    cases.append(
        {
            "id": existing["case_id"],
            "kind": "claim_dispute",
            "title": f"Disputed claim {existing['claim_id']} (Lakeshore Benefit Trust)",
            "summary": "Spinal fusion claim in formal dispute.",
            "data_class": "confidential",
            "members": {"marcus-reyes": "owner", "priya-nair": "member", "tom-becker": "member"},
        }
    )

    OUT.mkdir(exist_ok=True)
    files = {
        "patient-record.json": {"patients": patients, "encounters": encounters},
        "billing.json": {"claims": claims, "payers": payers},
        "erp.json": {"revenue_entries": revenue},
        "docket.json": {"legal_cases": legal_cases, "deadlines": deadlines},
        "contracts.json": {"contracts": contracts},
    }
    for name, content in files.items():
        (OUT / name).write_text(json.dumps(money_free(content), indent=1) + "\n")
    (ROOT / "cases.yaml").write_text(
        "# Case spaces of the demo, generated by seed/generate.py.\n"
        + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True)
    )
    print(
        f"wrote {len(patients)} patients, {len(claims)} claims, {len(contracts)} contracts, "
        f"{len(cases)} cases"
    )


if __name__ == "__main__":
    for line in (ROOT.parent / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    build()
