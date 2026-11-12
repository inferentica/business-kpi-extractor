"""Microsoft's commercial remaining performance obligation: in its releases from FY2026 only, tagged in every report."""
from datetime import date

import pandas as pd

from kpi_extractor.readers.tagged_metrics import Tag, agrees, learn_tag, quarter_facts

AXIS = "dim_srt_MajorCustomersAxis"


def frame(end: str, total: float, commercial: float, unit: str = "usd") -> pd.DataFrame:
    rows = [
        {"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": total, "period_type": "instant",
         "period_instant": end, "period_start": None, "period_end": None, AXIS: None},
        {"concept": "us-gaap:RevenueRemainingPerformanceObligation", "numeric_value": commercial, "period_type": "instant",
         "period_instant": end, "period_start": None, "period_end": None, AXIS: "msft:CommercialCustomersMember"},
        # A nine-month figure ending the same day is not the quarter's.
        {"concept": "us-gaap:Revenues", "numeric_value": commercial, "period_type": "duration", "period_instant": None,
         "period_start": "2025-07-01", "period_end": end, AXIS: None},
    ]
    return pd.DataFrame(rows).assign(unit_ref=unit, currency="USD" if unit == "usd" else None)


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
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric",
                                               "kpis": [{"key": "rpo", "label": "Commercial RPO", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "r4" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
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
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "backlog", "label": "Revenue Backlog", "unit": "currency"},
        {"key": "cloud_backlog", "label": "Google Cloud Backlog", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-Q", filed=day(2026, 1, 1), source_url="u") for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
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
    pipeline.store._values.update({(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in stored})
    pipeline._ask = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asked again"))
    stored.clear()
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))  # each tag is kept on its figures: no question, nothing left to fill
    assert stored == []


def test_a_timing_axis_reads_as_a_note_not_a_part():
    from kpi_extractor.readers.tagged_metrics import fact_lines
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
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "customers", "label": "Customers", "kind": "revenue_breakdown",
                                               "kpis": [{"key": "business", "label": "Business", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "k" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    assert {v["fiscal_period"]: v["value"] for v in stored} == {"Q1": 1_534e6, "Q2": 1_595e6, "Q3": 1_648e6, "Q4": 1_718e6}
    assert all(v["locator"]["total"] == 6e9 and v["notes"][0].startswith("parts make up") for v in stored)


def test_a_breakdowns_year_before_comes_from_the_reports_comparative_columns():
    # Adobe's FY2025 10-Qs tag FY2024's customer groups beside their own; its 10-K gives FY2023-25's years.
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline
    from kpi_extractor.readers.tagged_metrics import year_ago_facts
    ends = {"q1": "2025-02-28", "q2": "2025-05-30", "q3": "2025-08-29", "k": "2025-11-28"}
    columns = {"q1": [("2024-12-02", "2025-02-28", 1_534e6, 3_922e6), ("2023-12-02", "2024-03-01", 1_339e6, 3_550e6)],
               "q2": [("2025-03-01", "2025-05-30", 1_595e6, 4_019e6), ("2024-03-02", "2024-05-31", 1_380e6, 3_650e6)],
               "q3": [("2025-05-31", "2025-08-29", 1_648e6, 4_117e6), ("2024-06-01", "2024-08-30", 1_437e6, 3_715e6)],
               "k": [("2024-11-30", "2025-11-28", 6_495e6, 16_303e6), ("2023-12-02", "2024-11-29", 5_662e6, 14_749e6),
                     ("2022-12-03", "2023-12-01", 4_744e6, 13_425e6)]}
    members = {"business": "adbe:BusinessMember", "creative": "adbe:CreativeMember"}

    def frame_of(accession):
        return pd.DataFrame([{"concept": "us-gaap:Revenues", "numeric_value": value, "period_type": "duration",
                              "period_start": start, "period_end": end, "dim_srt_MajorCustomersAxis": members[kpi]}
                             for start, end, business, creative in columns[accession]
                             for kpi, value in (("business", business), ("creative", creative))])
    assert year_ago_facts(frame_of("q1"), day(2025, 2, 28))[0] == day(2024, 3, 1)
    pipeline = SymbolPipeline(None, "ADBE", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Adobe", fiscal_year_end="1128", foreign=False)
    pipeline.offset = 0
    pipeline.filings = {a: {"accession": a, "period_end": e, "status": "processed"} for a, e in ends.items()}
    pipeline.values = {}
    for kpi, index in (("business", 2), ("creative", 3)):
        for p, a in (("Q1", "q1"), ("Q2", "q2"), ("Q3", "q3"), ("Q4", "k")):
            exact = columns[a][0][index] if a != "k" else 1_718e6
            tagged = a != "k"
            pipeline.store._values[("kpi_customers", kpi, "2025", p)] = {
                "group_key": "kpi_customers", "group_label": "Customers", "group_kind": "revenue_breakdown", "group_order": 20,
                "kpi_key": kpi, "kpi_label": kpi.title(), "kpi_order": 0, "unit": "currency", "currency": "USD",
                "fiscal_year": "2025", "fiscal_period": p, "period_end": ends[a], "method": "ai", "validation_status": "verified",
                "source_accession": a, "value": exact, "notes": ["the 10-Q's exact figure; the release gave x"],
                "locator": {"tagged": True, **({"concept": "us-gaap:Revenues", "instant": False,
                                                "dimensions": {"dim_srt_MajorCustomersAxis": members[kpi]}} if tagged else {})}}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: frame_of(ref.accession)))
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "customers", "label": "Customers", "kind": "revenue_breakdown", "kpis": [
        {"key": "total", "label": "Total", "unit": "currency"},  # listed, never given by a release
        {"key": "business", "label": "Business", "unit": "currency"}, {"key": "creative", "label": "Creative", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "k" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    by = {(v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v["value"] for v in stored}
    assert by == {("business", "2024", "Q1"): 1_339e6, ("business", "2024", "Q2"): 1_380e6, ("business", "2024", "Q3"): 1_437e6,
                  ("creative", "2024", "Q1"): 3_550e6, ("creative", "2024", "Q2"): 3_650e6, ("creative", "2024", "Q3"): 3_715e6,
                  ("business", "2024", "FY"): 5_662e6, ("creative", "2024", "FY"): 14_749e6,
                  ("business", "2023", "FY"): 4_744e6, ("creative", "2023", "FY"): 13_425e6}
    q1 = next(v for v in stored if v["fiscal_period"] == "Q1")
    assert q1["period_end"] == "2024-03-01" and q1["source_accession"] == "q1" and q1["locator"]["year_ago"]
    assert q1["notes"] == ["the 10-Q's exact figure; its column for Q1 2024"]


def test_a_flagged_breakdown_quarter_whose_lines_are_all_tagged_exactly_is_settled():
    # KKR tags each asset management revenue line to the thousand but not their total.
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline
    ends = {"q1": "2026-03-31", "q2": "2026-06-30", "q3": "2026-09-30"}
    starts = {"q1": "2026-01-01", "q2": "2026-04-01", "q3": "2026-07-01"}
    figures = {"q1": (1_192_504e3, 252_709e3), "q2": (1_249_964e3, 221_269e3), "q3": (1_300_000e3, 230_000e3)}
    members = {"fees": "kkr:ManagementFeesMember", "transaction": "kkr:TransactionAndMonitoringFeesMember"}

    def frame_of(accession):
        return pd.DataFrame([{"concept": "us-gaap:Revenues", "numeric_value": value, "period_type": "duration",
                              "period_start": starts[accession], "period_end": ends[accession], "dim_srt_ProductOrServiceAxis": member}
                             for member, value in zip(members.values(), figures[accession])])
    pipeline = SymbolPipeline(None, "KKR", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="KKR", fiscal_year_end="1231", foreign=False)
    pipeline.offset = 0
    pipeline.filings = {a: {"accession": a, "period_end": e, "status": "processed"} for a, e in ends.items()}
    pipeline.values = {}
    read = {"Q1": (1_192_504e3, 252_709e3, 1_445_213e3), "Q2": (1_249_964e3, 221_269e3, 1_471_233e3),
            "Q3": (1_300_000e3, 230_001e3, 1_530_001e3)}  # Q3's second line is a thousand off its tag
    for period, (fees, transaction, total) in read.items():
        for kpi, value in (("fees", fees), ("transaction", transaction), ("total", total)):
            pipeline.store._values[("kpi_am", kpi, "2026", period)] = {
                "group_key": "kpi_am", "group_label": "AM", "group_kind": "revenue_breakdown", "group_order": 20, "kpi_key": kpi,
                "kpi_label": kpi, "kpi_order": 0, "unit": "currency", "currency": "USD", "fiscal_year": "2026", "fiscal_period": period,
                "period_end": ends[f"q{period[1]}"], "method": "ai", "validation_status": "needs_review", "source_accession": "release",
                "value": value, "notes": ["total matches no figure the company reported in XBRL"], "locator": {"total": total}}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: frame_of(ref.accession)))
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "am", "label": "AM", "kind": "revenue_breakdown", "total_kpi": "total", "kpis": [
        {"key": "total", "label": "Total", "unit": "currency"}, {"key": "fees", "label": "Fees", "unit": "currency"},
        {"key": "transaction", "label": "Transaction", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-Q", filed=day(2026, 11, 1), source_url="u") for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    settled = {(v["fiscal_period"], v["kpi_key"]) for v in stored if v["validation_status"] == "verified"}
    assert settled == {(p, k) for p in ("Q1", "Q2") for k in ("fees", "transaction", "total")}
    assert all(v["notes"] == ["the 10-Q's exact figure; every line of the quarter is tagged"] for v in stored)


def test_a_breakdown_quarter_missing_lines_its_report_tags_is_completed_when_it_then_adds_up():
    # JPMorgan's revenue: fee lines inside noninterest revenue, beside net interest income; a quarter's reading missed a
    # fee line and the subtotal, both in its 10-Q.
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline
    ends = {"q1": "2026-03-31", "q2": "2026-06-30", "q3": "2026-09-30"}
    starts = {"q1": "2026-01-01", "q2": "2026-04-01", "q3": "2026-07-01"}
    concepts = {"fees": "us-gaap:InvestmentBankingRevenue", "card": "us-gaap:FeesAndCommissionsCreditAndDebitCards",
                "nr": "us-gaap:NoninterestIncome", "nii": "us-gaap:InterestIncomeExpenseNet"}
    tagged = {"q1": {"fees": 3e9, "card": 1e9, "nr": 4e9, "nii": 5e9}, "q2": {"fees": 3.2e9, "card": 1.1e9, "nr": 4.3e9, "nii": 5.1e9},
              "q3": {"fees": 2.9e9, "card": 1.4e9, "nr": 4.3e9, "nii": 5.2e9}}

    def frame_of(accession):
        return pd.DataFrame([{"concept": concepts[kpi], "numeric_value": value, "period_type": "duration",
                              "period_start": starts[accession], "period_end": ends[accession]}
                             for kpi, value in tagged[accession].items()])
    pipeline = SymbolPipeline(None, "JPM", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="JPM", fiscal_year_end="1231", foreign=False)
    pipeline.offset = 0
    pipeline.filings = {a: {"accession": a, "period_end": e, "status": "processed"} for a, e in ends.items()}
    pipeline.values = {}
    read = {"Q1": tagged["q1"], "Q2": tagged["q2"], "Q3": {"fees": 2.9e9, "nii": 5.2e9}}
    for period, lines in read.items():
        total = sum(tagged[f"q{period[1]}"][kpi] for kpi in ("nr", "nii"))
        for kpi, value in lines.items():
            pipeline.store._values[("kpi_rev", kpi, "2026", period)] = {
                "group_key": "kpi_rev", "group_label": "Revenue", "group_kind": "revenue_breakdown", "group_order": 20,
                "kpi_key": kpi, "kpi_label": kpi, "kpi_order": 0, "unit": "currency", "currency": "USD", "fiscal_year": "2026",
                "fiscal_period": period, "period_end": ends[f"q{period[1]}"], "method": "ai",
                "validation_status": "needs_review", "source_accession": "release", "value": value,
                "notes": ["parts differ from the total by 40%"], "locator": {"total": total}}
    pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: frame_of(ref.accession)))
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.curation = lambda *_args: None
    spec = ai.Spec.model_validate({"groups": [{"key": "rev", "label": "Revenue", "kind": "revenue_breakdown", "total_kpi": "total", "kpis": [
        {"key": "fees", "label": "Fees", "unit": "currency", "parent": "nr"},
        {"key": "card", "label": "Card", "unit": "currency", "parent": "nr"},
        {"key": "nr", "label": "Noninterest revenue", "unit": "currency"}, {"key": "nii", "label": "NII", "unit": "currency"},
        {"key": "total", "label": "Total", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-Q", filed=day(2026, 11, 1), source_url="u") for a in ends]
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    q3 = {v["kpi_key"]: v["value"] for v in stored if v["fiscal_period"] == "Q3" and v["validation_status"] == "verified"}
    assert q3 == {"fees": 2.9e9, "nii": 5.2e9, "card": 1.4e9, "nr": 4.3e9}
    assert {(v["fiscal_period"], v["kpi_key"]) for v in stored} >= {(p, k) for p in ("Q1", "Q2") for k in ("fees", "card", "nr", "nii")}


def test_no_fitting_tag_is_remembered_so_the_same_report_is_not_asked_again():
    stored, asked, pipeline, reports, spec = _alphabet({"backlog": "Employees", "cloud_backlog": None})  # nothing fits
    assert len(asked) == 1 and not stored
    pipeline._ask = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("asked again"))
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))  # the next run: the same report, the same KPIs
    assert not stored


def test_a_figure_with_decimals_matches_only_to_its_own_rounding():
    from kpi_extractor.readers.tagged_metrics import agrees
    assert not agrees(3, 3.65)          # UCB: 3 interest-rate derivatives are no 3.65% margin
    assert not agrees(1.45, 1.21)       # FedEx: a $1.45 dividend is no $1.21 revenue per pound
    assert not agrees(32.79, 32.3)      # TSMC: a closing rate is no 32.30 average rate
    assert not agrees(10.21, 10.3)
    assert agrees(3.652, 3.65) and agrees(32.27, 32.3)
    assert agrees(676_800_000_000, 677_000_000_000) and agrees(7_643_972_000, 7_640_000_000)


def test_a_per_share_amount_or_a_count_of_things_fits_only_its_own_kind_of_kpi():
    from kpi_extractor.readers.tagged_metrics import unit_fits
    assert not unit_fits("currency", "USD/share", "International Revenue per Pound", "us-gaap:CommonStockDividendsPerShareDeclared")
    assert unit_fits("currency", "USD/share", "Dividends per Share", "us-gaap:CommonStockDividendsPerShareDeclared")
    assert not unit_fits("percent", "pure", "Net Interest Margin", "us-gaap:NumberOfInterestRateDerivativesHeld")
    assert not unit_fits("currency", "pure", "U.S. Freight Revenue per Pound", "us-gaap:NumberOfReportableSegments")
    assert unit_fits("count", "pure", "Reportable Segments", "us-gaap:NumberOfReportableSegments")
    assert unit_fits("currency", "USD", "Total Deposits", "us-gaap:Deposits")


def test_a_ratio_tagged_as_a_decimal_reads_as_a_percentage():
    from kpi_extractor.steps.tagged_kpis import _percent_scale
    from kpi_extractor.readers.tagged_metrics import Tag
    cet1 = Tag("mcbs:CommonEquityTierOneCapitalToRiskWeightedAssets", (), True)
    deposits = Tag("us-gaap:Deposits", (), True)
    scaled = _percent_scale({cet1: 0.1747, deposits: 1.2})
    assert scaled[cet1] == 17.47 and scaled[deposits] == 1.2


def test_a_tagged_figure_replaces_a_stated_one_only_to_more_places():
    from kpi_extractor.readers.tagged_metrics import refines
    assert refines(4_651_000_000, 4_650_000_000)        # Adobe: $4.65 billion to the dollar
    assert not refines(4_650_000_000, 4_650_000_000)    # the same figure: nothing to refine
    assert not refines(3, 3.65)                         # UCB: a count is no margin
    assert not refines(32.79, 32.3)                     # TSMC: a closing rate is no average rate
    assert refines(462_300_000_000, 460_000_000_000)      # "$460 billion" is rounded to ten billion: $462.3B refines it
    assert not refines(13_487_000_000, 13_760_000_000)    # a different figure never replaces a stated one


def test_a_round_release_figure_is_matched_to_two_significant_digits():
    from kpi_extractor.readers.tagged_metrics import refines
    # Brookfield's "$20 billion" of assets sold is not its 15.258 billion escrowed options, nor "$400 million" 410 million.
    assert not agrees(15.258e9, 20e9) and not refines(15.258e9, 20e9)
    assert agrees(19.6e9, 20e9) and not agrees(410e6, 400e6) and agrees(403e6, 400e6)
    assert agrees(9.038e9, 9e9) and refines(4_651_000_000, 4.65e9)


def test_a_facts_unit_is_read_through_its_resolved_currency():
    from kpi_extractor.readers.tagged_metrics import fact_unit, unit_fits
    assert fact_unit({"unit_ref": "Unit_Standard_EUR_DzninQPyI02xgP6HIeCj9A", "currency": "EUR"}) == "iso4217:EUR"
    assert fact_unit({"unit_ref": "Unit_Divide_EUR_shares_rFrgpyX0", "currency": "EUR"}) == "iso4217:EUR/shares"
    assert fact_unit({"unit_ref": "shares", "currency": None}) == "shares"
    assert unit_fits("currency", fact_unit({"unit_ref": "Unit_Standard_EUR_x", "currency": "EUR"}))
    assert not unit_fits("currency", fact_unit({"unit_ref": "shares", "currency": None}))


def test_a_tag_whose_unit_is_not_the_kpis_is_never_learned_and_what_it_filled_is_withdrawn():
    from datetime import date as day
    from types import SimpleNamespace

    from kpi_extractor import ai
    from kpi_extractor.pipeline import SymbolPipeline

    # The commercial figure is a count of shares here: equal to the releases' dollar figures, but another measure.
    ends = {"r1": "2025-09-30", "r2": "2025-12-31", "r3": "2026-03-31", "r4": "2026-06-30"}
    commercial = {"r1": 392e9, "r2": 625e9, "r3": 627e9, "r4": 678e9}

    def pipeline_with(values):
        pipeline = SymbolPipeline(None, "MSFT", quarters=4, force=False, log=lambda *_: None)
        pipeline.profile = SimpleNamespace(name="Microsoft", fiscal_year_end="0630", foreign=False)
        pipeline.offset = 0
        pipeline.filings = {accession: {"accession": accession, "period_end": end, "status": "processed"}
                            for accession, end in ends.items()}
        pipeline._xbrl = lambda ref: SimpleNamespace(facts=SimpleNamespace(
            to_dataframe=lambda: frame(ends[ref.accession], commercial[ref.accession] + 6e9, commercial[ref.accession],
                                       unit="shares")))
        pipeline.values = values
        stored = []
        pipeline.store.write = stored.extend
        pipeline.store.curation = lambda *_args: None
        return pipeline, stored

    def row(period, value, locator=None):
        return {"group_key": "kpi_operating", "group_kind": "metric", "group_label": "Operating", "group_order": 20,
                "kpi_key": "rpo", "kpi_label": "Commercial RPO", "kpi_order": 0, "unit": "currency", "currency": "USD",
                "fiscal_year": "2026", "fiscal_period": period, "period_end": "", "method": "ai",
                "validation_status": "verified", "value": value, "locator": locator or {}}
    spec = ai.Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric",
                                               "kpis": [{"key": "rpo", "label": "Commercial RPO", "unit": "currency"}]}]})
    reports = [SimpleNamespace(accession=a, form="10-K" if a == "r4" else "10-Q", filed=day(2026, 1, 1), source_url="u")
               for a in ends]
    releases = {("kpi_operating", "rpo", "2026", q): row(q, v) for q, v in (("Q1", 392e9), ("Q3", 627e9), ("Q4", 678e9))}
    pipeline, stored = pipeline_with(dict(releases))
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    assert stored == []  # not learned: no quarter is filled from it
    # A figure an earlier run filled from that tag is withdrawn.
    filled = row("Q2", 625e9, {"tagged": True, "concept": COMMERCIAL.concept, "dimensions": dict(COMMERCIAL.dimensions),
                               "instant": True})
    pipeline, stored = pipeline_with({**releases, ("kpi_operating", "rpo", "2026", "Q2"): filled})
    pipeline.store.write(pipeline._fill_tagged_metrics(reports, spec))
    assert [(v["fiscal_period"], v["validation_status"]) for v in stored] == [("Q2", "rejected")]
    assert stored[0]["notes"][0].startswith("withdrawn: its report tag (RevenueRemainingPerformanceObligation)")


def test_a_valuation_models_input_is_never_a_kpis_figure():
    # KKR tags the weighted-average EV/EBITDA exit multiple behind its Level 3 fair values (11.0, then 11.7). It equalled
    # the release's "infrastructure portfolio appreciated 11%" twice, so it was learned as that return.
    multiple = {"concept": "kkr:EquityMethodInvestmentsOtherMeasurementInput", "numeric_value": 11.7, "period_type": "instant",
                "period_instant": "2025-06-30", "period_start": None, "period_end": None, "unit_ref": "pure",
                "dim_us-gaap_MeasurementInputTypeAxis": "kkr:MeasurementInputEnterpriseValueEBITDAExitMultipleMember",
                "dim_srt_RangeAxis": "srt:WeightedAverageMember"}
    tagged_input = {**multiple, "concept": "us-gaap:FairValueMeasurementInput", "dim_us-gaap_MeasurementInputTypeAxis": None,
                    "dim_srt_RangeAxis": None}
    kept = {**multiple, "concept": "us-gaap:Revenues", "numeric_value": 9.0, "dim_us-gaap_MeasurementInputTypeAxis": None,
            "dim_srt_RangeAxis": None}
    found = quarter_facts(pd.DataFrame([multiple, tagged_input, kept]), date(2025, 6, 30))
    assert found == {Tag("us-gaap:Revenues", (), True): 9.0}


def test_a_coarse_whole_reading_learns_a_tag_only_from_the_same_figure_exactly():
    from kpi_extractor.readers.tagged_metrics import Tag, learn_tags
    other = Tag("kkr:SomeOtherPercentage", (), False)
    returns = Tag("kkr:LeveragedCreditReturn", (), False)
    readings = {("2024", "Q4"): 7.0, ("2025", "Q2"): 9.0}
    # 7.3 and 8.8 agree with "7%" and "9%" within half a unit, but are no evidence of their tag; 7.0 and 9.0 are.
    facts = {("2024", "Q4"): {other: 7.3, returns: 7.0}, ("2025", "Q2"): {other: 8.8, returns: 9.0}}
    assert learn_tags(readings, facts) == [returns]
    # A larger or more precise figure keeps its tolerance: "$677 billion" is 676.5 to 677.5 billion.
    assert learn_tags({("2024", "Q4"): 677e9, ("2025", "Q2"): 702e9},
                      {("2024", "Q4"): {other: 677.2e9}, ("2025", "Q2"): {other: 701.8e9}}) == [other]


def test_a_tag_is_learned_from_two_different_figures_never_a_run_of_equal_ones():
    from kpi_extractor.readers.tagged_metrics import Tag, learn_tags
    rate = Tag("us-gaap:DebtWeightedAverageInterestRate", (), False)
    # KKR's leveraged credit return 5.25% two quarters running; its junior notes' rate 5.25% both times.
    assert learn_tags({("2026", "Q1"): 5.25, ("2026", "Q2"): 5.25},
                      {("2026", "Q1"): {rate: 5.25}, ("2026", "Q2"): {rate: 5.25}}) == []
    assert learn_tags({("2026", "Q1"): 5.25, ("2026", "Q2"): 5.8},
                      {("2026", "Q1"): {rate: 5.25}, ("2026", "Q2"): {rate: 5.8}}) == [rate]
