"""Everything the demo computes in code: deadlines, tax and the denial pipeline totals.

The model never does this arithmetic. Agents call a tool, the tool calls one of these
functions, and the answer carries the rule or the records it came from.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

#: US federal holidays observed in the demo period (fixed list keeps the demo deterministic).
HOLIDAYS = frozenset(
    date.fromisoformat(d)
    for d in (
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
        "2026-09-07",
        "2026-10-12",
        "2026-11-11",
        "2026-11-26",
        "2026-12-25",
        "2027-01-01",
        "2027-01-18",
        "2027-02-15",
        "2027-05-31",
        "2027-06-18",
        "2027-07-05",
        "2027-09-06",
        "2027-10-11",
        "2027-11-11",
        "2027-11-25",
        "2027-12-24",
    )
)


@dataclass(frozen=True)
class DeadlineRule:
    """A period that starts on an event and ends on a due date."""

    key: str
    title: str
    days: int
    #: calendar: count every day. business: count working days only.
    counting: str
    source: str


DEADLINE_RULES: dict[str, DeadlineRule] = {
    rule.key: rule
    for rule in (
        DeadlineRule(
            "payer_appeal_level_1",
            "First-level appeal to the payer",
            60,
            "calendar",
            "Payer agreement, clause 7.3: 60 calendar days from the denial notice",
        ),
        DeadlineRule(
            "payer_appeal_level_2",
            "Second-level appeal to the payer",
            30,
            "calendar",
            "Payer agreement, clause 7.4: 30 calendar days from the first-level decision",
        ),
        DeadlineRule(
            "notice_of_dispute",
            "Formal notice of dispute to the payer",
            30,
            "calendar",
            "Payer agreement, clause 12.2: within 30 calendar days of the final denial",
        ),
        DeadlineRule(
            "notice_response",
            "Payer's response to a notice of dispute",
            45,
            "calendar",
            "Payer agreement, clause 12.3: 45 calendar days from receipt of the notice",
        ),
        DeadlineRule(
            "arbitration_demand",
            "Demand for arbitration",
            20,
            "business",
            "Payer agreement, clause 12.4: 20 business days after the response period",
        ),
    )
}


def is_business_day(day: date) -> bool:
    """Return whether offices are open on ``day``."""
    return day.weekday() < 5 and day not in HOLIDAYS


def next_business_day(day: date) -> date:
    """Return ``day`` itself if it is a business day, otherwise the next one."""
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def compute_deadline(rule_key: str, event_date: date) -> dict[str, Any]:
    """Return the due date for a rule and an event date, with how it was computed.

    Calendar periods count every day after the event and roll forward when they end on a
    weekend or holiday. Business periods count working days only.
    """
    rule = DEADLINE_RULES.get(rule_key)
    if rule is None:
        raise KeyError(rule_key)
    if rule.counting == "calendar":
        raw = event_date + timedelta(days=rule.days)
        due = next_business_day(raw)
        note = (
            f"{rule.days} calendar days after {event_date.isoformat()} is "
            f"{raw.isoformat()} ({raw.strftime('%A')})"
        )
        if due != raw:
            note += f"; not a business day, so it rolls forward to {due.isoformat()}"
    else:
        due, counted = event_date, 0
        while counted < rule.days:
            due += timedelta(days=1)
            counted += is_business_day(due)
        note = f"{rule.days} business days after {event_date.isoformat()}"
    return {
        "rule": rule.key,
        "title": rule.title,
        "event_date": event_date.isoformat(),
        "due_date": due.isoformat(),
        "weekday": due.strftime("%A"),
        "computation": note,
        "source": rule.source,
    }


#: Stages of a denied claim, in order, with the label the director sees.
DENIAL_STAGES = {
    "denied": "Denied, appeal not filed yet",
    "appealed": "Appeal filed, waiting for the payer",
    "appeal_denied": "Appeal denied, not escalated yet",
    "in_dispute": "In formal dispute (legal)",
}


def denial_summary(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Return count and amount of unresolved denied claims by stage, with the claim ids."""
    stages = []
    total_count, total_cents = 0, 0
    for status, label in DENIAL_STAGES.items():
        matching = [c for c in claims if c["status"] == status]
        cents = sum(int(c["billed_cents"]) for c in matching)
        total_count += len(matching)
        total_cents += cents
        stages.append(
            {
                "stage": status,
                "label": label,
                "claims": len(matching),
                "amount": dollars(cents),
                "claim_ids": sorted(c["id"] for c in matching),
            }
        )
    return {
        "stages": stages,
        "total_claims": total_count,
        "total_amount": dollars(total_cents),
        "source": "billing system, claims with an unresolved denial",
    }


#: Flat corporate income tax rate used by the demo tax engine.
TAX_RATE_BASIS_POINTS = 2100
#: Share of a disputed receivable that is reserved until the dispute is resolved.
DISPUTE_RESERVE_BASIS_POINTS = 10000


def run_tax_engine(period: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the month-end tax treatment of a period from its revenue entries.

    Disputed revenue is fully reserved and excluded from the taxable base until the dispute is
    resolved. All arithmetic is in integer cents.
    """
    in_period = [e for e in entries if e["period"] == period]
    gross = sum(int(e["amount_cents"]) for e in in_period)
    disputed_entries = [e for e in in_period if e["status"] == "disputed"]
    disputed = sum(int(e["amount_cents"]) for e in disputed_entries)
    reserve = disputed * DISPUTE_RESERVE_BASIS_POINTS // 10000
    taxable = gross - reserve
    provision = taxable * TAX_RATE_BASIS_POINTS // 10000
    return {
        "period": period,
        "entries": len(in_period),
        "gross_revenue": dollars(gross),
        "disputed_revenue": dollars(disputed),
        "dispute_reserve": dollars(reserve),
        "taxable_revenue": dollars(taxable),
        "tax_rate": f"{TAX_RATE_BASIS_POINTS / 100:.2f}%",
        "income_tax_provision": dollars(provision),
        "disputed_claim_ids": sorted(e["claim_id"] for e in disputed_entries),
        "source": "ERP revenue ledger and the tax engine, computed in code",
    }


def dollars(cents: int) -> str:
    """Format integer cents as a dollar amount, for example ``$48,750.00``."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{fraction:02d}"
