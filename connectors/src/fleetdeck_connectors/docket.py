"""Connector for the docket: legal matters and deadlines computed from rules."""

from fleetdeck_connectors.base import Deps, Result, Tool, safe

INSTRUCTIONS = (
    "The docket of the legal department. Due dates are computed by the docket from its rules; "
    "never count days yourself. Opening a matter and sending a notice are drafts until the "
    "lawyer confirms them."
)


def tools(deps: Deps) -> list[Tool]:
    """Return the docket tools bound to ``deps``."""

    @safe
    async def list_deadline_rules() -> Result:
        """List the deadline rules the docket knows, with their periods and contract sources."""
        await deps.allow("list_deadline_rules")
        return {"rules": await deps.systems.get("/docket/rules")}

    @safe
    async def compute_deadline(rule: str, event_date: str) -> Result:
        """Compute a due date from a rule and the date of the event that starts the period.

        Args:
            rule: A rule key from list_deadline_rules, for example notice_of_dispute.
            event_date: The date of the triggering event as YYYY-MM-DD, or "today".

        """
        await deps.allow("compute_deadline", record=f"{rule} from {event_date}")
        return dict(await deps.systems.get("/docket/deadline", rule=rule, event_date=event_date))

    @safe
    async def list_matters() -> Result:
        """List the legal matters of the cases I am a member of."""
        await deps.allow("list_matters")
        cases = await deps.control.member_cases(deps.token())
        matters = await deps.systems.get("/docket/cases", case_id=cases) if cases else []
        return {"matters": matters}

    @safe
    async def get_matter(matter_id: str) -> Result:
        """Return a legal matter with its notices and deadlines.

        Args:
            matter_id: The matter id, for example LM-2026-014.

        """
        matter = await deps.systems.get(f"/docket/cases/{matter_id}")
        await deps.allow("get_matter", case_id=matter["case_id"], record=matter_id)
        return dict(matter)

    @safe
    async def open_matter_draft(claim_id: str, title: str, counterparty: str) -> Result:
        """Prepare opening a legal matter for a disputed claim. The lawyer confirms it.

        Args:
            claim_id: The claim in dispute.
            title: The title of the matter.
            counterparty: The other party, for example the payer's name.

        """
        claim = await deps.systems.get(f"/billing/claims/{claim_id}")
        decision = await deps.allow(
            "open_matter_draft", case_id=claim.get("case_id"), record=f"matter for {claim_id}"
        )
        draft = await deps.systems.post(
            "/docket/drafts/open-case",
            {
                "author": decision.identity.person_id,
                "claim_id": claim_id,
                "title": title,
                "counterparty": counterparty,
            },
        )
        return await deps.register_draft(decision, draft)

    @safe
    async def formal_notice_draft(matter_id: str, text: str) -> Result:
        """Prepare a formal notice of dispute. The lawyer reviews and sends it.

        When the lawyer sends it, the docket records the payer's response deadline itself.

        Args:
            matter_id: The legal matter the notice belongs to.
            text: The full text of the notice.

        """
        matter = await deps.systems.get(f"/docket/cases/{matter_id}")
        decision = await deps.allow(
            "formal_notice_draft", case_id=matter["case_id"], record=f"notice for {matter_id}"
        )
        draft = await deps.systems.post(
            "/docket/drafts/formal-notice",
            {"author": decision.identity.person_id, "legal_case_id": matter_id, "text": text},
        )
        return await deps.register_draft(decision, draft)

    @safe
    async def list_deadlines() -> Result:
        """List the open deadlines of the matters in my cases, soonest first."""
        await deps.allow("list_deadlines")
        cases = await deps.control.member_cases(deps.token())
        rows = await deps.systems.get("/docket/deadlines", case_id=cases) if cases else []
        return {"deadlines": rows}

    @safe
    async def docket_summary() -> Result:
        """Return the open legal matters with their stage and next deadline."""
        await deps.allow("docket_summary")
        return dict(await deps.systems.get("/docket/summary"))

    return [
        list_deadline_rules,
        compute_deadline,
        list_matters,
        get_matter,
        open_matter_draft,
        formal_notice_draft,
        list_deadlines,
        docket_summary,
    ]
