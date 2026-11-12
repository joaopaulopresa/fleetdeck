"""Connector for the contract repository."""

from fleetdeck_connectors.base import Deps, Result, Tool, safe

INSTRUCTIONS = (
    "The contract repository of Meridian Health Group: payer and supplier agreements. Quote "
    "clauses exactly as returned, with their clause number."
)


def tools(deps: Deps) -> list[Tool]:
    """Return the contract tools bound to ``deps``."""

    @safe
    async def search_contracts(query: str) -> Result:
        """Search contracts and clauses by words; returns the best matching clauses.

        Args:
            query: Words to look for, for example "Northwind notice of dispute".

        """
        await deps.allow("search_contracts", record=query[:60])
        return {"matches": await deps.systems.get("/contracts", query=query)}

    @safe
    async def get_contract(contract_id: str) -> Result:
        """Return a contract's parties, dates and the list of its clauses.

        Args:
            contract_id: The contract id, for example CTR-NORTHWIND-2025.

        """
        await deps.allow("get_contract", record=contract_id)
        return dict(await deps.systems.get(f"/contracts/{contract_id}"))

    @safe
    async def get_clause(contract_id: str, clause_id: str) -> Result:
        """Return the full text of one clause.

        Args:
            contract_id: The contract id.
            clause_id: The clause number, for example 12.2.

        """
        await deps.allow("get_clause", record=f"{contract_id} clause {clause_id}")
        return dict(await deps.systems.get(f"/contracts/{contract_id}/clauses/{clause_id}"))

    return [search_contracts, get_contract, get_clause]
