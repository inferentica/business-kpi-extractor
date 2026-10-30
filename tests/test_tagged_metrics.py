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


def _alphabet(picks):
    """Alphabet's revenue backlog: in every 10-Q, in no release. Its Google Cloud part is tagged from Q1 2026."""
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline

    ends = {"r1": "2025-12-31", "r2": "2026-03-31", "r3": "2026-06-30"}
    total = {"r1": 242.8e9, "r2": 467.6e9, "r3": 519.5e9}
    cloud = {"r2": 462.3e9, "r3": 513.9e9}

    def frame_of(accession):
        rows = [{"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": total[accession], "period_type": "instant",
                 "period_instant": ends[accession], "period_start": None, "period_end": None, "label": "Revenue not yet recognized",
                 "unit_ref": "usd", "dim_srt_ProductOrServiceAxis": None}]
        if accession in cloud:
            rows.append({**rows[0], "numeric_value": cloud[accession], "dim_srt_ProductOrServiceAxis": "goog:GoogleCloudMember"})
        rows.append({**rows[0], "concept": "dei:EntityNumberOfEmployees", "numeric_value": 190_000, "unit_ref": "number", "label": "Employees"})
        return pd.DataFrame(rows)

    pipeline = SymbolPipeline(None, "GOOGL", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Alphabet", fiscal_year_end="1231", foreign=False)
    pipeline.offset = 0
    pipeline.filings = {a: {"accession": a, "period_end": e, "status": "processed"} for a, e in ends.items()}
    # The Q1 2026 release's one mention, rounded: "backlog nearly doubling ... to over $460 billion".
    pipeline.values = {("kpi_operating", "cloud_backlog", "2026", "Q1"): {
        "group_key": "kpi_operating", "group_label": "Operating", "group_kind": "metric", "group_order": 20, "kpi_key": "cloud_backlog",
        "kpi_label": "Google Cloud Backlog", "kpi_order": 1, "unit": "currency", "currency": "USD", "fiscal_year": "2026",
        "fiscal_period": "Q1", "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": 460e9,
        "source_accession": "release", "locator": {"value_text": "$460 billion"}}}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: frame_of(ref.accession)))
    asked = []

    def ask(text, purpose, schema, thinking_first, model="flash"):
        asked.append(text)
        lines = {line.split("] ", 1)[1]: line.split("]")[0][1:] for line in text.splitlines() if line.startswith("[F")}
        return ai.TagPicks(picks={key: next((id_ for label, id_ in lines.items() if want and label.startswith(want)), None)
                                  for key, want in picks.items()})
    pipeline._ask = ask
    stored = []
    pipeline._store = lambda values: stored.extend(values)
    spec = ai.Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "backlog", "label": "Revenue Backlog", "unit": "currency"},
        {"key": "cloud_backlog", "label": "Google Cloud Backlog", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-Q", filed=day(2026, 1, 1), source_url="u") for a in ends]
    pipeline._fill_tagged_metrics(reports, spec)
    return stored, asked, pipeline, reports, spec


def test_a_kpi_no_release_states_is_read_from_the_tag_the_ai_points_to():
    stored, asked, *_ = _alphabet({
        "backlog": "Revenue not yet recognized [RevenueRemainingPerformanceObligation] | 519",
        "cloud_backlog": "Revenue not yet recognized [RevenueRemainingPerformanceObligation] | Google Cloud"})
    by = {(v["kpi_key"], v["fiscal_period"], v["fiscal_year"]): v["value"] for v in stored}
    # Q1's rounded release figure gives way to the 10-Q's exact one.
    assert by == {("backlog", "Q4", "2025"): 242.8e9, ("backlog", "Q1", "2026"): 467.6e9, ("backlog", "Q2", "2026"): 519.5e9,
                  ("cloud_backlog", "Q1", "2026"): 462.3e9, ("cloud_backlog", "Q2", "2026"): 513.9e9}
    q1 = next(v for v in stored if v["kpi_key"] == "cloud_backlog" and v["fiscal_period"] == "Q1")
    assert q1["notes"] == ["the 10-Q's exact figure; the release gave $460 billion"]
    assert len(asked) == 1 and all(v["locator"]["tagged"] for v in stored)


def test_a_pick_whose_unit_does_not_fit_is_refused_and_a_found_tag_needs_no_ai():
    stored, _, *_ = _alphabet({"backlog": "Employees"})  # a count for an amount
    assert not [v for v in stored if v["kpi_key"] == "backlog"]
    stored, asked, pipeline, reports, spec = _alphabet({
        "backlog": "Revenue not yet recognized [RevenueRemainingPerformanceObligation] | 519",
        "cloud_backlog": "Revenue not yet recognized [RevenueRemainingPerformanceObligation] | Google Cloud"})
    pipeline.values.update({(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in stored})
    pipeline._ask = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asked again"))
    stored.clear()
    pipeline._fill_tagged_metrics(reports, spec)  # each tag is kept on its figures: no question, nothing left to fill
    assert stored == []


def test_a_timing_axis_reads_as_a_note_not_a_part():
    from kpi_extractor.tagged_metrics import fact_lines
    frame = pd.DataFrame([{"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": 496e9, "period_type": "instant",
                           "period_instant": "2026-06-30", "period_start": None, "period_end": None, "label": "2026-07-01", "unit_ref": "usd",
                           "dim_us-gaap_RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis": "2026-07-01"}])
    [(_tag, value, label, unit)] = fact_lines(frame, date(2026, 6, 30))
    assert label == "Revenue Remaining Performance Obligation [RevenueRemainingPerformanceObligation]"  # the whole: no part
    # The next quarter's figure is the same tag, though its timing date moved.
    later = frame.assign(period_instant="2026-09-30", numeric_value=540e9, **{
        "dim_us-gaap_RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis": "2026-10-01"})
    assert set(quarter_facts(later, date(2026, 9, 30))) == {_tag}


def test_a_breakdowns_rounded_lines_take_the_reports_exact_figures():
    # Adobe's releases give its customer groups to $10 million; its 10-Qs tag them exactly, its 10-K for the year only.
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline
    ends = {"q1": "2025-02-28", "q2": "2025-05-30", "q3": "2025-08-29", "k": "2025-11-28"}
    starts = {"q1": "2024-11-30", "q2": "2025-03-01", "q3": "2025-05-31", "k": "2024-11-30"}
    exact = {"q1": 1_534e6, "q2": 1_595e6, "q3": 1_648e6, "k": 6_495e6}

    def frame_of(accession):
        return pd.DataFrame([{"concept": "us-gaap:Revenues", "numeric_value": exact[accession], "period_type": "duration",
                              "period_start": starts[accession], "period_end": ends[accession], "label": "Revenue",
                              "unit_ref": "usd", "dim_srt_MajorCustomersAxis": "adbe:BusinessMember"}])
    pipeline = SymbolPipeline(None, "ADBE", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Adobe", fiscal_year_end="1128", foreign=False)
    pipeline.offset = 0
    pipeline.filings = {a: {"accession": a, "period_end": e, "status": "processed"} for a, e in ends.items()}
    base = {"group_key": "kpi_customers", "group_label": "Customers", "group_kind": "revenue_breakdown", "group_order": 20,
            "kpi_key": "business", "kpi_label": "Business", "kpi_order": 0, "unit": "currency", "currency": "USD",
            "fiscal_year": "2025", "method": "ai", "validation_status": "verified", "source_accession": "release",
            "notes": ["parts make up nearly all of a figure reported in XBRL"], "locator": {"total": 6e9}}
    pipeline.values = {("kpi_customers", "business", "2025", p): {**base, "fiscal_period": p, "period_end": ends[a], "value": v}
                       for p, a, v in (("Q1", "q1", 1.53e9), ("Q2", "q2", 1.60e9), ("Q3", "q3", 1.65e9), ("Q4", "k", 1.72e9))}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: frame_of(ref.accession)))
    stored = []
    pipeline._store = lambda values: stored.extend(values)
    spec = ai.Spec.model_validate({"groups": [{"key": "customers", "label": "Customers", "kind": "revenue_breakdown",
                                               "kpis": [{"key": "business", "label": "Business", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "k" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    pipeline._fill_tagged_metrics(reports, spec)
    assert {v["fiscal_period"]: v["value"] for v in stored} == {"Q1": 1_534e6, "Q2": 1_595e6, "Q3": 1_648e6, "Q4": 1_718e6}
    assert all(v["locator"]["total"] == 6e9 and v["notes"][0].startswith("parts make up") for v in stored)
