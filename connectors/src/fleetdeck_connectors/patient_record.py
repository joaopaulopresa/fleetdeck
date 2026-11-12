"""Connector for the patient record. Holds restricted data: physicians only, own patients only."""

from fleetdeck_connectors.base import Deps, Result, Tool, safe

INSTRUCTIONS = (
    "The patient record of Meridian Health Group. Only the treating physician's agent can read "
    "a chart. Notes you write are drafts until the physician signs them."
)


def tools(deps: Deps) -> list[Tool]:
    """Return the patient-record tools bound to ``deps``."""

    @safe
    async def list_my_patients() -> Result:
        """List the patients I am the treating physician of, with their patient ids."""
        await deps.allow("list_my_patients")
        cases = await deps.control.member_cases(deps.token())
        patients = (
            await deps.systems.get("/patient-record/patients", case_id=cases) if cases else []
        )
        return {"patients": patients}

    @safe
    async def get_patient_summary(patient_id: str) -> Result:
        """Return a patient's chart summary: problems, medications, allergies and encounters.

        Args:
            patient_id: The patient id, for example PAT-0001.

        """
        chart = await deps.systems.get(f"/patient-record/patients/{patient_id}")
        await deps.allow(
            "get_patient_summary", case_id=chart["case_id"], record=f"{patient_id} chart"
        )
        return dict(chart)

    @safe
    async def get_encounter_note(encounter_id: str) -> Result:
        """Return one encounter with its full clinical note and results.

        Args:
            encounter_id: The encounter id from the chart summary, for example ENC-000102.

        """
        encounter = await deps.systems.get(f"/patient-record/encounters/{encounter_id}")
        await deps.allow(
            "get_encounter_note", case_id=encounter["case_id"], record=f"{encounter_id} note"
        )
        return dict(encounter)

    @safe
    async def draft_clinical_note(patient_id: str, title: str, text: str) -> Result:
        """Prepare a clinical note for the physician to review and sign. It is not signed.

        Args:
            patient_id: The patient id.
            title: A short title for the note.
            text: The note itself.

        """
        chart = await deps.systems.get(f"/patient-record/patients/{patient_id}")
        decision = await deps.allow(
            "draft_clinical_note", case_id=chart["case_id"], record=f"{patient_id} note draft"
        )
        draft = await deps.systems.post(
            "/patient-record/drafts/note",
            {
                "author": decision.identity.person_id,
                "patient_id": patient_id,
                "title": title,
                "text": text,
            },
        )
        return await deps.register_draft(decision, draft)

    return [list_my_patients, get_patient_summary, get_encounter_note, draft_clinical_note]
