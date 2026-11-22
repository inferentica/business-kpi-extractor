"""Segment operating income as Alphabet and Nvidia tag it in their Q3 2026 / Q2 FY2027 10-Qs."""
from datetime import date

import pandas as pd

from kpi_extractor.readers.segment_income import segment_income

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


def test_only_the_business_segments_are_rows_and_they_add_up_to_their_own_sum():
    # Alphabet-level activities (−7,655) are no segment: the rows are the three segments, and their total is their sum.
    rows = quarter(39_157e6, 11_121e6, -2_047e6, -7_655e6, 40_576e6) + quarter(33_527e6, 3_594e6, -1_426e6, -4_467e6, 31_228e6,
                                                                              "2025-07-01", "2025-09-30")
    income = segment_income(pd.DataFrame(rows), date(2026, 9, 30), False, LABELS.get)
    assert [(name, value) for _key, name, _element, value in income.current.rows] == [
        ("Google Services", 39_157e6), ("Google Cloud", 11_121e6), ("Other Bets", -2_047e6)]
    assert income.current.total == 48_231e6 and income.prior.total == 35_695e6
    assert income.current.lines == set()


def test_what_the_segments_leave_of_operating_income_is_no_row():
    # Nvidia tags its corporate item with the segments' own sum, and the segments' sum on the consolidation axis alone.
    start, end = "2026-04-27", "2026-07-26"
    rows = [fact(62_700e6, start, end, "nvda:ComputeAndNetworkingSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(3_900e6, start, end, "nvda:GraphicsSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(66_590e6, start, end, None, "us-gaap:OperatingSegmentsMember"),
            fact(66_590e6, start, end, None, "us-gaap:CorporateNonSegmentMember"),
            fact(63_730e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 7, 26), False, {}.get)
    assert [round(value / 1e6) for *_rest, value in income.current.rows] == [62_700, 3_900]
    assert income.current.total == 66_600e6


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
    assert "Total Intel Products" not in names and sum(value for *_rest, value in income.current.rows) == income.current.total
    declared = segment_income(pd.DataFrame(rows), date(2026, 3, 28), False, {}.get,
                              {"intc:TotalIntelProductsMember": {"intc:CCPGMember", "intc:DCAIMember"}})
    assert len(declared.current.rows) == len(income.current.rows)


def test_a_reconciling_item_on_the_segments_axis_is_no_segment():
    # AMD's Q2 2026 10-Q tags "All Other" as a corporate item on the segments axis: it is left out.
    start, end = "2026-03-29", "2026-06-27"
    rows = [fact(2_103e6, start, end, "amd:DataCenterMember", "us-gaap:OperatingSegmentsMember"),
            fact(582e6, start, end, "amd:ClientAndGamingMember", "us-gaap:OperatingSegmentsMember"),
            fact(386e6, start, end, "amd:EmbeddedMember", "us-gaap:OperatingSegmentsMember"),
            fact(-1_081e6, start, end, "amd:AllOtherMember", "us-gaap:CorporateNonSegmentMember"),
            fact(1_990e6, start, end)]
    income = segment_income(pd.DataFrame(rows), date(2026, 6, 27), False, {"amd:AllOtherMember": "All Other"}.get)
    assert [key for key, *_rest in income.current.rows] == ["datacenter", "clientandgaming", "embedded"]
    assert income.current.total == 3_071e6


def test_a_companys_own_segment_measures_stand_in_for_operating_income():
    # Brookfield Asset Management tags no operating income by segment; its "Segment Earnings" (fee-related earnings)
    # and "Segment Revenue" are its own measures, beside its GAAP revenue by segment.
    from kpi_extractor.readers.segment_income import segment_revenue
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


def test_two_segments_one_label_keep_their_own_names():
    # Brookfield's 2026 Q3 10-Q labels its Energy segment's member "Infrastructure"; its table says Energy (179).
    from kpi_extractor.readers.segment_income import segment_revenue
    start, end = "2026-07-01", "2026-09-30"
    members = ("bam:CreditSegmentMember", "bam:RealEstateSegmentMember", "bam:InfrastructureMember", "bam:EnergySegmentMember",
               "bam:PrivateEquityMember")
    rows = [{**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": "bam:SegmentRevenue"}
            for member, value in zip(members, (562e6, 283e6, 268e6, 179e6, 170e6))]
    labels = {"bam:InfrastructureMember": "Infrastructure", "bam:EnergySegmentMember": "Infrastructure",
              "bam:CreditSegmentMember": "Credit", "bam:RealEstateSegmentMember": "Real estate", "bam:PrivateEquityMember": "Private equity"}
    revenue = segment_revenue(pd.DataFrame(rows), date(2026, 9, 30), False, labels.get)
    assert {key: value for key, _name, _member, value in revenue.current.rows} == {
        "credit": 562e6, "realestate": 283e6, "infrastructure": 268e6, "energy": 179e6, "privateequity": 170e6}


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


def test_a_companys_own_contribution_is_its_segments_income():
    # Palantir's Q2 2026 10-Q tags each segment's contribution (pltr:Contribution, beside its margin) but no operating
    # income by segment; the costs between its contribution and its operating income are no segment.
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
    assert income.current.total == 1_436_050e3
    assert [(key, value) for key, _name, _element, value in income.current.rows] == [
        ("commercial", 734_944e3), ("government", 701_106e3)]
    assert income.prior.total == 637_988e3
    # Without the operating income it reconciles to, a contribution is no breakdown.
    assert segment_income(pd.DataFrame([row for row in rows if row["concept"] != "us-gaap:OperatingIncomeLoss"]),
                          date(2026, 6, 30), False) is None


def test_a_corporate_column_and_its_cost_lines_are_no_rows():
    # Apple's Q1 FY2026 10-Q: the Corporate column's operating loss (12,982) and the research and development (10,887)
    # and general and administrative (2,095) inside it are left out; the segments add up to 41,743.
    start, end, item = "2025-09-28", "2025-12-27", "us-gaap:CorporateNonSegmentMember"
    rows = [fact(23_953e6, start, end, "aapl:AmericasSegmentMember"), fact(17_790e6, start, end, "aapl:EuropeSegmentMember"),
            fact(-12_982e6, start, end, None, item), fact(28_761e6, start, end),
            {**fact(10_887e6, start, end, None, item), "concept": "us-gaap:ResearchAndDevelopmentExpense"},
            {**fact(2_095e6, start, end, None, item), "concept": "us-gaap:GeneralAndAdministrativeExpense"}]
    names = {"aapl:AmericasSegmentMember": "Americas", "aapl:EuropeSegmentMember": "Europe", item: "Corporate non-segment"}
    income = segment_income(pd.DataFrame(rows), date(2025, 12, 27), False, names.get)
    assert [(key, value) for key, _name, _element, value in income.current.rows] == [("americas", 23_953e6), ("europe", 17_790e6)]
    assert income.current.total == 41_743e6


def test_a_segments_operating_segment_suffix_is_not_part_of_its_name():
    start, end = "2025-01-01", "2025-12-31"
    rows = [fact(1_576e6, start, end, "pltr:GovernmentOperatingSegmentMember", "us-gaap:OperatingSegmentsMember"),
            fact(1_367e6, start, end, "pltr:CommercialOperatingSegmentMember", "us-gaap:OperatingSegmentsMember"), fact(2_943e6, start, end)]
    names = {"pltr:GovernmentOperatingSegmentMember": "Government Operating Segment", "pltr:CommercialOperatingSegmentMember": "Commercial"}
    income = segment_income(pd.DataFrame(rows), date(2025, 12, 31), True, names.get)
    assert [row[:2] for row in income.current.rows] == [("government", "Government"), ("commercial", "Commercial")]


def test_a_banks_segments_on_a_tax_equivalent_basis_are_its_revenue_by_segment():
    # Bank of America's FY2025 10-K: its four segments and All Other (corporate reconciling items and eliminations) add
    # up to its total revenue on a fully taxable-equivalent basis, 113,706, which its GAAP revenue (113,097) is less the
    # 609 FTE adjustment.
    from kpi_extractor.readers.segment_income import segment_revenue
    start, end, concept = "2025-01-01", "2025-12-31", "bac:RevenuesNetOfInterestExpenseFullTaxEquivalentBasis"
    rows = [{**fact(value, start, end, member, "us-gaap:OperatingSegmentsMember"), "concept": concept} for member, value in (
        ("bac:ConsumerBankingSegmentMember", 43_673e6), ("bac:GlobalWealthAndInvestmentManagementSegmentMember", 24_883e6),
        ("bac:GlobalBankingSegmentMember", 24_108e6), ("bac:GlobalMarketsSegmentMember", 24_096e6))]
    rows += [{**fact(-3_054e6, start, end, None, "us-gaap:CorporateReconcilingItemsAndEliminationsMember"), "concept": concept},
             {**fact(113_706e6, start, end), "concept": concept},
             {**fact(113_097e6, start, end), "concept": "us-gaap:Revenues"}]
    labels = {"us-gaap:CorporateReconcilingItemsAndEliminationsMember": "All Other"}
    revenue = segment_revenue(pd.DataFrame(rows), date(2025, 12, 31), True, labels.get)
    assert revenue.label == "Revenue by Segment (FTE basis)" and revenue.current.total == 113_706e6
    rows_read = {name: value for _key, name, _member, value in revenue.current.rows}
    assert rows_read["All Other"] == -3_054e6 and sum(rows_read.values()) == 113_706e6 and len(rows_read) == 5
