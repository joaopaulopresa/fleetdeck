"""Connector for the ERP: revenue ledger, month-end close and the tax engine."""

from fleetdeck_connectors.base import Deps, Result, Tool, safe

INSTRUCTIONS = (
    "The ERP of Meridian Health Group. Every figure comes from the ledger or the tax engine; "
    "never compute tax yourself. Ledger changes are drafts until the tax analyst posts them."
)


def tools(deps: Deps) -> list[Tool]:
    """Return the ERP tools bound to ``deps``."""

    @safe
    async def get_revenue_entry(claim_id: str) -> Result:
        """Return the revenue entry booked for a claim: period, amount and status.

        Args:
            claim_id: The claim number.

        """
        await deps.allow("get_revenue_entry", record=claim_id)
        return dict(await deps.systems.get(f"/erp/revenue/{claim_id}"))

    @safe
    async def flag_disputed_draft(claim_id: str, reason: str) -> Result:
        """Prepare marking a claim's revenue as disputed. The tax analyst confirms the posting.

        Args:
            claim_id: The claim whose revenue is in dispute.
            reason: Why, with the reference of the legal matter.

        """
        decision = await deps.allow("flag_disputed_draft", record=f"{claim_id} revenue")
        draft = await deps.systems.post(
            "/erp/drafts/flag-disputed",
            {"author": decision.identity.person_id, "claim_id": claim_id, "reason": reason},
        )
        return await deps.register_draft(decision, draft)

    @safe
    async def run_tax_engine(period: str) -> Result:
        """Run the tax engine for a period and return revenue, reserve and tax provision.

        Args:
            period: The period as YYYY-MM, for example 2026-09.

        """
        await deps.allow("run_tax_engine", record=period)
        return dict(await deps.systems.get("/erp/tax-engine", period=period))

    @safe
    async def month_end_status(period: str) -> Result:
        """Return the month-end close status of a period, listing only the exceptions.

        Args:
            period: The period as YYYY-MM.

        """
        await deps.allow("month_end_status", record=period)
        return dict(await deps.systems.get("/erp/month-end", period=period))

    return [get_revenue_entry, flag_disputed_draft, run_tax_engine, month_end_status]
