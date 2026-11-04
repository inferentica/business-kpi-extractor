"""Segment operating income as Alphabet and Nvidia tag it in their Q3 2026 / Q2 FY2027 10-Qs."""
from datetime import date

import pandas as pd

from kpi_extractor.segment_income import line_key, segment_income

SEG, CONS = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_ConsolidationItemsAxis"


def fact(value, start, end, segment=None, item=None):
    return {"concept": "us-gaap:OperatingIncomeLoss", "numeric_value": value, "period_type": "duration",
            "period_start": start, "period_end": end, SEG: segment, CONS: item}


def quarter(services, cloud, bets, alphabet_level, total, start="2026-07-01", end="2026-09-30"):
    return [fact(services, start, end, "goog:GoogleServicesMember", "us-gaap:OperatingSegmentsMember"),
            fact(cloud, start, end, "goog:GoogleCloudMember", "us-gaap:OperatingSegmentsMember"),
            fact(bets, start, end, "us-gaap:AllOtherSegmentsMember", "us-gaap:OperatingSegmentsMember"),
            fact(alphabet_level, start, end, None, "us-gaap:CorporateNonSegmentMember"),
            fact(total, start, end)]


LABELS = {"goog:GoogleServicesMember": "Google Services", "goog:GoogleCloudMember": "Google Cloud",
          "us-gaap:AllOtherSegmentsMember": "Other Bets", "us-gaap:CorporateNonSegmentMember": "Reconciling items"}


def test_segments_and_the_activities_outside_them_add_up_to_operating_income():
    rows = quarter(39_157e6, 11_121e6, -2_047e6, -7_655e6, 40_576e6) + quarter(33_527e6, 3_594e6, -1_426e6, -4_467e6, 31_228e6,
                                                                              "2025-07-01", "2025-09-30")
    income = segment_income(pd.DataFrame(rows), date(2026, 9, 30), False, LABELS.get)
    assert [(name, value) for _key, name, _element, value in income.current.rows] == [
        ("Google Services", 39_157e6), ("Google Cloud", 11_121e6), ("Other Bets", -2_047e6), ("Reconciling items", -7_655e6)]
    assert income.current.total == 40_576e6 and income.prior.total == 31_228e6


def test_a_tagged_item_that_does_not_close_the_gap_gives_way_to_the_gap_itself():
    # Nvidia tags its corporate item with the segments' own sum, and the segments' sum on the consolidation axis alone.
    start, end = "2026-04-27", "2026-07-26"
    rows = [fact(62_700e6, start, end, "nvda:ComputeAndNetworkingSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(3_900e6, start, end, "nvda:GraphicsSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(66_590e6, start, end, None, "us-gaap:OperatingSegmentsMember"),
            fact(66_590e6, start, end, None, "us-gaap:CorporateNonSegmentMember"),
            fact(63_730e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 7, 26), False, {}.get)
    assert [(name, round(value / 1e6)) for _key, name, _element, value in income.current.rows][-1] == ("Unallocated", -2_870)
    assert income.current.total == 63_730e6


def test_one_segment_is_no_breakdown():
    rows = [fact(5e9, "2026-07-01", "2026-09-30", "x:OnlyMember", "us-gaap:OperatingSegmentsMember"), fact(5e9, "2026-07-01", "2026-09-30")]
    assert segment_income(pd.DataFrame(rows), date(2026, 9, 30), False) is None


def test_a_segment_that_adds_up_others_is_left_out():
    # Intel tags "Total Intel Products" beside CCPG and DCAI on the same axis.
    start, end = "2026-01-01", "2026-03-28"
    rows = [fact(4_060e6, start, end, "intc:TotalIntelProductsMember", "us-gaap:OperatingSegmentsMember"),
            fact(2_520e6, start, end, "intc:CCPGMember", "us-gaap:OperatingSegmentsMember"),
            fact(1_540e6, start, end, "intc:DCAIMember", "us-gaap:OperatingSegmentsMember"),
            fact(-2_440e6, start, end, "intc:IntelFoundryMember", "us-gaap:OperatingSegmentsMember"),
            fact(-3_140e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 3, 28), False, {}.get)
    names = [name for _key, name, _element, _value in income.current.rows]
    assert "Total Intel Products" not in names and sum(value for *_rest, value in income.current.rows) == -3_140e6
    declared = segment_income(pd.DataFrame(rows), date(2026, 3, 28), False, {}.get,
                              {"intc:TotalIntelProductsMember": {"intc:CCPGMember", "intc:DCAIMember"}})
    assert len(declared.current.rows) == len(income.current.rows)


def test_a_reconciling_item_on_the_segments_axis_keeps_its_name():
    # AMD's Q2 2026 10-Q tags "All Other" as a corporate item on the segments axis.
    start, end = "2026-03-29", "2026-06-27"
    rows = [fact(2_103e6, start, end, "amd:DataCenterMember", "us-gaap:OperatingSegmentsMember"),
            fact(582e6, start, end, "amd:ClientAndGamingMember", "us-gaap:OperatingSegmentsMember"),
            fact(386e6, start, end, "amd:EmbeddedMember", "us-gaap:OperatingSegmentsMember"),
            fact(-1_081e6, start, end, "amd:AllOtherMember", "us-gaap:CorporateNonSegmentMember"),
            fact(1_990e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 6, 27), False, {"amd:AllOtherMember": "All Other"}.get)
    assert income.current.rows[-1][1:] == ("All Other", "amd:AllOtherMember", -1_081e6)


def test_a_companys_own_segment_measures_stand_in_for_operating_income():
    # Brookfield Asset Management tags no operating income by segment; its "Segment Earnings" (fee-related earnings)
    # and "Segment Revenue" are its own measures, beside its GAAP revenue by segment.
    from kpi_extractor.segment_income import segment_revenue
    start, end = "2026-04-01", "2026-06-30"
    members = ("bam:CreditSegmentMember", "bam:InfrastructureMember", "bam:RealEstateSegmentMember",
               "bam:EnergySegmentMember", "bam:PrivateEquityMember")

    def measure(concept, values, total):
        return [{**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": concept}
                for member, value in zip(members, values)] + [{**fact(total, start, end), "concept": concept}]
    rows = measure("bam:SegmentEarnings", (208e6, 157e6, 125e6, 109e6, 41e6), 640e6) \
        + measure("bam:SegmentRevenue", (400e6, 300e6, 260e6, 200e6, 157e6), 1_317e6) \
        + [{**fact(919e6, start, end), "concept": "us-gaap:OperatingIncomeLoss"}]  # untagged by segment
    earnings = segment_income(pd.DataFrame(rows), date(2026, 6, 30), False)
    assert earnings.concept == "bam:SegmentEarnings" and earnings.label == "Earnings by Segment"
    assert [value for *_rest, value in earnings.current.rows] == [208e6, 157e6, 125e6, 109e6, 41e6]
    revenue = segment_revenue(pd.DataFrame(rows), date(2026, 6, 30), False)
    assert revenue.label == "Revenue by Segment" and revenue.current.total == 1_317e6
    # Alphabet's tagged operating income is still what it reads, and it has no measure of its own.
    alphabet = pd.DataFrame(quarter(39_157e6, 11_121e6, -2_047e6, -7_655e6, 40_576e6))
    assert segment_income(alphabet, date(2026, 9, 30), False).concept == "us-gaap:OperatingIncomeLoss"
    assert segment_revenue(alphabet, date(2026, 9, 30), False) is None


def test_a_companys_own_measure_without_its_total_is_its_segments_added_up():
    # Brookfield's Q1 2026 10-Q tags each segment's earnings for the quarter, but not their sum.
    start, end = "2026-01-01", "2026-03-31"
    members = ("bam:CreditSegmentMember", "bam:InfrastructureMember", "bam:RealEstateSegmentMember")
    rows = [{**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": "bam:SegmentEarnings"}
            for member, value in zip(members, (183e6, 174e6, 121e6))]
    earnings = segment_income(pd.DataFrame(rows), date(2026, 3, 31), False)
    assert earnings.current.total == 478e6 and len(earnings.current.rows) == 3
    # Operating income still needs its own total.
    tagged = [{**row, "concept": "us-gaap:OperatingIncomeLoss"} for row in rows]
    assert segment_income(pd.DataFrame(tagged), date(2026, 3, 31), False) is None


def test_a_companys_own_contribution_adds_up_to_operating_income():
    # Palantir's Q2 2026 10-Q tags each segment's contribution (pltr:Contribution, beside its margin) but no operating
    # income by segment; its untagged operating income is what the segments and the costs they leave out add up to.
    rows = []
    for (start, end), (government, commercial, total) in {("2026-04-01", "2026-06-30"): (701_106e3, 734_944e3, 912_004e3),
                                                          ("2025-04-01", "2025-06-30"): (350_412e3, 287_576e3, 269_317e3)}.items():
        for member, value in (("pltr:GovernmentOperatingSegmentMember", government),
                              ("pltr:CommercialOperatingSegmentMember", commercial)):
            rows.append({**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": "pltr:Contribution"})
            rows.append({**fact(70, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": "pltr:ContributionMargin"})
        rows.append(fact(total, start, end))
    names = {"pltr:GovernmentOperatingSegmentMember": "Government", "pltr:CommercialOperatingSegmentMember": "Commercial"}
    income = segment_income(pd.DataFrame(rows), date(2026, 6, 30), False, names.get)
    assert income.concept == "pltr:Contribution" and income.label == "Operating Income by Segment"
    assert income.current.total == 912_004e3
    assert [(key, value) for key, _name, _element, value in income.current.rows] == [
        ("commercial", 734_944e3), ("government", 701_106e3), ("unallocated", -524_046e3)]
    assert income.prior.total == 269_317e3
    # Without the operating income it reconciles to, a contribution is no breakdown.
    assert segment_income(pd.DataFrame([row for row in rows if row["concept"] != "us-gaap:OperatingIncomeLoss"]),
                          date(2026, 6, 30), False) is None


def test_a_reconciliations_tagged_lines_keep_the_filings_names():
    # Palantir's Q3 2026 10-Q: its costs between total contribution and income from operations are tagged on the
    # consolidation axis, and they add up to exactly what the segments leave.
    start, end = "2026-07-01", "2026-09-30"
    item = "us-gaap:MaterialReconcilingItemsMember"
    rows = []
    for member, value in (("pltr:GovernmentOperatingSegmentMember", 797_258e3), ("pltr:CommercialOperatingSegmentMember", 897_525e3)):
        rows.append({**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": "pltr:Contribution"})
    rows.append(fact(1_084_845e3, start, end))
    rows.append(fact(1_084_845e3, start, end, None, item))
    costs = {"pltr:ResearchAndDevelopment": 154_506e3, "pltr:GeneralAndAdministrative": 136_543e3, "us-gaap:AllocatedShareBasedCompensationExpense": 318_889e3}
    for concept, value in costs.items():
        rows.append({**fact(value, start, end, None, item), "concept": concept})
    names = {"pltr:GovernmentOperatingSegmentMember": "Government", "pltr:CommercialOperatingSegmentMember": "Commercial"}
    shown = {"pltr:ResearchAndDevelopment": "Research and development expenses", "pltr:GeneralAndAdministrative": "General and administrative expenses",
             "us-gaap:AllocatedShareBasedCompensationExpense": "Total stock-based compensation expense"}
    income = segment_income(pd.DataFrame(rows), date(2026, 9, 30), False, names.get, None, shown.get)
    assert [(key, name, value) for key, name, _element, value in income.current.rows] == [
        ("commercial", "Commercial", 897_525e3), ("government", "Government", 797_258e3),
        ("stockbasedcompensation", "Total stock-based compensation expense", -318_889e3),
        ("researchanddevelopment", "Research and development expenses", -154_506e3),
        ("generalandadministrative", "General and administrative expenses", -136_543e3)]
    assert sum(value for *_rest, value in income.current.rows) == income.current.total
    assert income.current.lines == {"stockbasedcompensation", "researchanddevelopment", "generalandadministrative"}


def test_tagged_lines_that_do_not_close_the_gap_are_no_reconciliation():
    # Coca-Cola tags revenue and capital spending on the same axis: nothing of them names the gap.
    start, end = "2026-07-01", "2026-09-30"
    rows = [fact(600e6, start, end, "ko:AMember", "us-gaap:OperatingSegmentsMember"), fact(500e6, start, end, "ko:BMember", "us-gaap:OperatingSegmentsMember"),
            fact(1_000e6, start, end), {**fact(38e6, start, end, None, "us-gaap:CorporateNonSegmentMember"), "concept": "us-gaap:Revenues"}]
    income = segment_income(pd.DataFrame(rows), date(2026, 9, 30), False, {}.get, None, {"us-gaap:Revenues": "Net operating revenues"}.get)
    assert income.current.rows[-1][1] == "Unallocated" and income.current.rows[-1][3] == -100e6


def test_one_item_that_is_the_gap_stands_beside_the_segments_own_sum():
    # Coca-Cola tags its segments' sum as one reconciling item and Corporate as another.
    start, end = "2026-07-01", "2026-10-02"
    rows = [fact(600e6, start, end, "ko:AMember", "us-gaap:OperatingSegmentsMember"), fact(500e6, start, end, "ko:BMember", "us-gaap:OperatingSegmentsMember"),
            fact(1_100e6, start, end, None, "us-gaap:MaterialReconcilingItemsMember"), fact(-100e6, start, end, None, "us-gaap:CorporateNonSegmentMember"),
            fact(1_000e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 10, 2), False, {}.get)
    assert income.current.rows[-1][2:] == ("us-gaap:CorporateNonSegmentMember", -100e6)
    assert "Unallocated" not in [row[1] for row in income.current.rows]


def test_a_segments_operating_segment_suffix_is_not_part_of_its_name():
    start, end = "2025-01-01", "2025-12-31"
    rows = [fact(1_576e6, start, end, "pltr:GovernmentOperatingSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(1_367e6, start, end, "pltr:CommercialOperatingSegmentMember", "us-gaap:OperatingSegmentsMember"), fact(2_943e6, start, end)]
    names = {"pltr:GovernmentOperatingSegmentMember": "Government Operating Segment", "pltr:CommercialOperatingSegmentMember": "Commercial"}
    income = segment_income(pd.DataFrame(rows), date(2025, 12, 31), True, names.get)
    assert [row[:2] for row in income.current.rows] == [("government", "Government"), ("commercial", "Commercial")]


def test_a_reconciling_lines_names_over_the_years_are_one_series():
    for name in ("Research and development", "Research and development expenses",
                 "Research and Development Expense Excluding Stockbased Compensation Expense"):
        assert line_key(name) == "researchanddevelopment"
    for name in ("Stock-based compensation expense", "Total stock-based compensation expense"):
        assert line_key(name) == "stockbasedcompensation"


def test_the_one_set_of_lines_that_is_the_gap_is_found_among_others():
    # Nvidia tags more than its reconciliation's lines on the corporate item.
    start, end = "2026-01-26", "2026-04-26"
    item = "us-gaap:CorporateNonSegmentMember"
    rows = [fact(53_335e6, start, end, "nvda:C", "us-gaap:OperatingSegmentsMember"), fact(2_941e6, start, end, "nvda:G", "us-gaap:OperatingSegmentsMember"),
            fact(53_536e6, start, end)]
    lines = {"us-gaap:ShareBased": 1_928e6, "nvda:Unallocated": 565e6, "nvda:Acquisition": 247e6, "us-gaap:Depreciation": 391e6}
    rows += [{**fact(value, start, end, None, item), "concept": concept} for concept, value in lines.items()]
    names = {"us-gaap:ShareBased": "Stock-based compensation expense", "nvda:Unallocated": "Unallocated operating expenses",
             "nvda:Acquisition": "Acquisition-related and other costs", "us-gaap:Depreciation": "Depreciation"}
    income = segment_income(pd.DataFrame(rows), date(2026, 4, 26), False, {}.get, None, names.get)
    assert [row[3] for row in income.current.rows[2:]] == [-1_928e6, -565e6, -247e6]


def test_a_total_beside_its_parts_names_nothing():
    start, end = "2026-01-01", "2026-03-31"
    item = "us-gaap:CorporateNonSegmentMember"
    rows = [fact(600e6, start, end, "x:A", "us-gaap:OperatingSegmentsMember"), fact(500e6, start, end, "x:B", "us-gaap:OperatingSegmentsMember"), fact(1_000e6, start, end)]
    lines = {"x:One": 40e6, "x:Two": 60e6, "x:All": 100e6}
    rows += [{**fact(value, start, end, None, item), "concept": concept} for concept, value in lines.items()]
    income = segment_income(pd.DataFrame(rows), date(2026, 3, 31), False, {}.get, None, {"x:One": "One", "x:Two": "Two", "x:All": "All"}.get)
    assert income.current.rows[-1][1] == "Unallocated"
