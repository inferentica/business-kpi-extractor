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


def test_the_pipeline_fills_the_quarters_the_releases_skip():
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline

    ends = {"r1": "2025-09-30", "r2": "2025-12-31", "r3": "2026-03-31", "r4": "2026-06-30"}
    commercial = {"r1": 392e9, "r2": 625e9, "r3": 627e9, "r4": 678e9}
    pipeline = SymbolPipeline(None, "MSFT", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Microsoft", fiscal_year_end="0630", foreign=False)
    pipeline.offset = 0
    # As the control plane returns them: a period end, no fiscal label.
    pipeline.filings = {accession: {"accession": accession, "period_end": end, "status": "processed"} for accession, end in ends.items()}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(
        to_dataframe=lambda: frame(ends[ref.accession], commercial[ref.accession] + 6e9, commercial[ref.accession])))

    def row(period, value):
        return {"group_key": "kpi_operating", "group_kind": "metric", "group_label": "Operating", "group_order": 20,
                "kpi_key": "rpo", "kpi_label": "Commercial RPO", "kpi_order": 0, "unit": "currency", "currency": "USD",
                "fiscal_year": "2026", "fiscal_period": period, "period_end": "", "method": "ai",
                "validation_status": "verified", "value": value, "locator": {}}
    pipeline.values = {("kpi_operating", "rpo", "2026", q): row(q, v) for q, v in (("Q1", 392e9), ("Q3", 627e9), ("Q4", 678e9))}
    stored = []
    pipeline._store = lambda values: stored.extend(values)
    spec = ai.Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric",
                                               "kpis": [{"key": "rpo", "label": "Commercial RPO", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "r4" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    pipeline._fill_tagged_metrics(reports, spec)
    assert [(v["fiscal_period"], v["value"], v["method"], v["locator"]["tagged"]) for v in stored] == [("Q2", 625e9, "ai", True)]
