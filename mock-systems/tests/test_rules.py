from datetime import date

import pytest
from fleetdeck_mock import rules


def test_calendar_deadline_rolls_past_weekends_and_holidays() -> None:
    # 60 days after Tuesday 6 Oct 2026 is Saturday 5 Dec: due the following Monday.
    due = rules.compute_deadline("payer_appeal_level_1", date(2026, 10, 6))
    assert (due["due_date"], due["weekday"]) == ("2026-12-07", "Monday")
    assert "rolls forward" in due["computation"] and "clause 7.3" in due["source"]
    # Ends on a plain weekday: no roll.
    assert rules.compute_deadline("notice_response", date(2026, 10, 9))["due_date"] == "2026-11-23"
    # 30 days after 26 Oct 2026 is Wednesday 25 Nov; one day later it is Thanksgiving.
    assert (
        rules.compute_deadline("notice_of_dispute", date(2026, 10, 26))["due_date"] == "2026-11-25"
    )
    assert (
        rules.compute_deadline("notice_of_dispute", date(2026, 10, 27))["due_date"] == "2026-11-27"
    )


def test_business_day_deadline_skips_weekends_and_holidays() -> None:
    # 20 business days from Friday 13 Nov 2026, skipping Thanksgiving (26 Nov).
    due = rules.compute_deadline("arbitration_demand", date(2026, 11, 13))
    assert due["due_date"] == "2026-12-14"
    assert rules.is_business_day(date(2026, 11, 26)) is False


def test_unknown_rule_is_an_error_not_a_guess() -> None:
    with pytest.raises(KeyError):
        rules.compute_deadline("statute_of_limitations", date(2026, 1, 1))


def test_tax_engine_reserves_disputed_revenue_in_integer_cents() -> None:
    entries = [
        {"claim_id": "A", "period": "2026-09", "amount_cents": 4_875_000, "status": "disputed"},
        {"claim_id": "B", "period": "2026-09", "amount_cents": 8_018_033, "status": "recognized"},
        {"claim_id": "C", "period": "2026-08", "amount_cents": 999_999, "status": "recognized"},
    ]
    result = rules.run_tax_engine("2026-09", entries)
    assert result["gross_revenue"] == "$128,930.33"
    assert result["disputed_revenue"] == result["dispute_reserve"] == "$48,750.00"
    assert result["taxable_revenue"] == "$80,180.33"
    assert result["income_tax_provision"] == "$16,837.86"  # 8,018,033 * 21% = 1,683,786.93 cents
    assert result["disputed_claim_ids"] == ["A"]


def test_denial_summary_counts_only_unresolved_denials_and_names_its_sources() -> None:
    claims = [
        {"id": "C1", "status": "denied", "billed_cents": 4_875_000},
        {"id": "C2", "status": "in_dispute", "billed_cents": 6_120_000},
        {"id": "C3", "status": "paid", "billed_cents": 1_000_000},
        {"id": "C4", "status": "denied", "billed_cents": 21_500},
    ]
    summary = rules.denial_summary(claims)
    by_stage = {s["stage"]: s for s in summary["stages"]}
    assert (by_stage["denied"]["claims"], by_stage["denied"]["amount"]) == (2, "$48,965.00")
    assert by_stage["denied"]["claim_ids"] == ["C1", "C4"]
    assert by_stage["in_dispute"]["amount"] == "$61,200.00"
    assert (summary["total_claims"], summary["total_amount"]) == (3, "$110,165.00")


def test_dollars() -> None:
    assert rules.dollars(0) == "$0.00"
    assert rules.dollars(4_875_000) == "$48,750.00"
    assert rules.dollars(-105) == "-$1.05"
