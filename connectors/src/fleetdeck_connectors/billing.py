"""Connector for billing and claims."""

from fleetdeck_connectors.base import Deps, Result, Tool, safe

INSTRUCTIONS = (
    "Billing and claims of Meridian Health Group: claims, denials and appeals. Amounts and "
    "totals are computed by the system. Appeals are drafts until the biller files them."
)


def tools(deps: Deps) -> list[Tool]:
    """Return the billing tools bound to ``deps``."""

    @safe
    async def list_denials(status: str = "") -> Result:
        """List claims with an unresolved denial.

        Args:
            status: Optional filter: denied, appealed, appeal_denied or in_dispute. Empty for all.

        """
        await deps.allow("list_denials")
        statuses = [status] if status else ["denied", "appealed", "appeal_denied", "in_dispute"]
        return {"claims": await deps.systems.get("/billing/claims", status=statuses)}

    @safe
    async def get_claim(claim_id: str) -> Result:
        """Return a claim: patient name, procedure, payer, billed amount, denial and appeals.

        Args:
            claim_id: The claim number, for example CLM-2026-01187.

        """
        claim = await deps.systems.get(f"/billing/claims/{claim_id}")
        await deps.allow("get_claim", case_id=claim.get("case_id"), record=claim_id)
        return dict(claim)

    @safe
    async def file_appeal_draft(claim_id: str, justification: str) -> Result:
        """Prepare an appeal for a denied claim. The biller confirms it before it is filed.

        Args:
            claim_id: The denied claim.
            justification: The full text of the appeal, including the clinical justification.

        """
        claim = await deps.systems.get(f"/billing/claims/{claim_id}")
        decision = await deps.allow(
            "file_appeal_draft", case_id=claim.get("case_id"), record=f"{claim_id} appeal draft"
        )
        draft = await deps.systems.post(
            "/billing/drafts/appeal",
            {
                "author": decision.identity.person_id,
                "claim_id": claim_id,
                "justification": justification,
            },
        )
        return await deps.register_draft(decision, draft)

    @safe
    async def denial_summary() -> Result:
        """Return how many claims and how much money are in each stage of an unresolved denial.

        Use this for totals. The figures are computed by the billing system and come with the
        claim numbers they are made of.
        """
        await deps.allow("denial_summary")
        return dict(await deps.systems.get("/billing/denial-summary"))

    return [list_denials, get_claim, file_appeal_draft, denial_summary]
