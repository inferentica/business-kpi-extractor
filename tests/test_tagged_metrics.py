"""Microsoft's commercial remaining performance obligation: in its releases from FY2026 only, tagged in every report."""
from datetime import date

import pandas as pd

from kpi_extractor.tagged_metrics import Tag, agrees, learn_tag, quarter_facts

AXIS = "dim_srt_MajorCustomersAxis"


def frame(end: str, total: float, commercial: float) -> pd.DataFrame:
    rows = [
        {"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": total, "period_type": "instant",
         "period_instant": end, "period_start": None, "period_end": None, AXIS: None},
        {"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": commercial, "period_type": "instant",
         "period_instant": end, "period_start": None, "period_end": None, AXIS: "msft:CommercialCustomersMember"},
        # A nine-month figure ending the same day is not the quarter's.
        {"concept": "us-gaap:Revenues", "numeric_value": commercial, "period_type": "duration", "period_instant": None,
         "period_start": "2025-07-01", "period_end": end, AXIS: None},
    ]
    return pd.DataFrame(rows)


COMMERCIAL = Tag("us-gaap:RevenueRemainingPerformanceObligation", ((AXIS, "msft:CommercialCustomersMember"),), True)


def test_the_quarters_facts_are_its_instants_and_three_month_durations():
    found = quarter_facts(frame("2026-03-31", 633e9, 627e9), date(2026, 3, 31))
    assert found == {Tag("us-gaap:RevenueRemainingPerformanceObligation", (), True): 633e9, COMMERCIAL: 627e9}


def test_a_release_figure_matches_to_its_own_rounding():
    assert agrees(676.8e9, 677e9) and not agrees(675.9e9, 677e9)
    assert agrees(49_123_000_000, 49.1e9) and not agrees(49_200_000_000, 49.1e9)


def test_the_tag_is_learned_from_two_quarters_and_refused_on_a_contradiction():
    facts = {("2026", "Q3"): quarter_facts(frame("2026-03-31", 633e9, 627e9), date(2026, 3, 31)),
             ("2026", "Q2"): quarter_facts(frame("2025-12-31", 631e9, 625e9), date(2025, 12, 31))}
    assert learn_tag({("2026", "Q3"): 627e9, ("2026", "Q2"): 625e9}, facts) == COMMERCIAL
    assert learn_tag({("2026", "Q3"): 627e9}, facts) is None  # one quarter could be a coincidence
    facts[("2026", "Q1")] = quarter_facts(frame("2025-09-30", 398e9, 380e9), date(2025, 9, 30))
    assert learn_tag({("2026", "Q3"): 627e9, ("2026", "Q2"): 625e9, ("2026", "Q1"): 392e9}, facts) is None
