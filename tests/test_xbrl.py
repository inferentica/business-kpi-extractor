import pandas as pd
import pytest

from kpi_extractor.xbrl import drop_overlaps, extract_breakdowns, humanize, member_key, remove_subtotals


def test_remove_subtotals_keeps_leaves():
    members = {"DataCenter": 89_023, "Hyperscale": 48_710, "AIClouds": 40_313, "Edge": 7_198}
    assert remove_subtotals(members, 96_221) == {"Hyperscale": 48_710, "AIClouds": 40_313, "Edge": 7_198}


def test_remove_subtotals_leaves_a_partition_alone():
    members = {"iPhone": 54_252, "Services": 30_739, "Mac": 10_352, "Wearables": 7_883, "iPad": 6_191}
    assert remove_subtotals(members, 109_417) == members


def test_humanize():
    assert humanize("nvda:ChinaIncludingHongKongMember") == "China Including Hong Kong"
    assert humanize("aapl:AmericasSegmentMember") == "Americas"


def _fact(concept, value, member=None, axis="dim_srt_ProductOrServiceAxis", start="2026-04-01", end="2026-06-30",
          consolidation=None):
    row = {"concept": concept, "period_type": "duration", "period_start": start, "period_end": end,
           "numeric_value": value, "currency": "USD", "unit_ref": "USD", axis: member,
           "dim_srt_ConsolidationItemsAxis": consolidation}
    return row


def test_extract_breakdowns_reconciles_and_ignores_other_periods():
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact("us-gaap:Revenues", 60.0, "x:ProductAMember"),
        _fact("us-gaap:Revenues", 40.0, "x:ProductBMember"),
        _fact("us-gaap:Revenues", 999.0, "x:ProductAMember", start="2026-01-01"),  # six months, not the quarter
        _fact("us-gaap:Revenues", 70.0, "x:SegmentOneMember", axis="dim_us-gaap_StatementBusinessSegmentsAxis",
              consolidation="us-gaap:OperatingSegmentsMember"),
        _fact("us-gaap:Revenues", 30.0, "x:SegmentTwoMember", axis="dim_us-gaap_StatementBusinessSegmentsAxis",
              consolidation="us-gaap:OperatingSegmentsMember"),
    ])
    result = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30", "fiscal_year": "2026"}, "10-Q")
    assert [group.key for group in result.groups] == ["segments", "products"]
    products = result.groups[1]
    assert [(key, value) for key, _label, value in products.members] == [("producta", 60.0), ("productb", 40.0)]
    assert products.reconciliation_error == pytest.approx(0)
    assert result.total_revenue == 100.0


def test_extract_breakdowns_drops_groups_that_do_not_reconcile():
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact("us-gaap:Revenues", 60.0, "x:ProductAMember"),
        _fact("us-gaap:Revenues", 20.0, "x:ProductBMember"),
    ])
    result = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q")
    assert result.groups == []


def test_official_labels_prefer_the_filings_terse_label():
    from types import SimpleNamespace

    from kpi_extractor.xbrl import official_labels
    catalog = {
        "aapl_AmericasSegmentMember": SimpleNamespace(labels={
            "http://www.xbrl.org/2003/role/label": "Americas Segment [Member]",
            "http://www.xbrl.org/2003/role/terseLabel": "Americas",
        }),
        "tsm_MarketsOfCustomersAxis": SimpleNamespace(labels={"http://www.xbrl.org/2003/role/label": "Markets of customers [Axis]"}),
    }
    label = official_labels(catalog)
    assert label("aapl:AmericasSegmentMember") == "Americas"
    assert label("tsm:MarketsOfCustomersAxis") == "Markets of customers"
    assert label("x:UnknownMember") is None


def test_custom_breakdowns_get_a_revenue_by_title():
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact("us-gaap:Revenues", 60.0, "x:HpcMember", axis="dim_x_MarketsOfCustomersAxis"),
        _fact("us-gaap:Revenues", 40.0, "x:SmartphoneMember", axis="dim_x_MarketsOfCustomersAxis"),
    ])
    result = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q",
                                lambda qname: {"x:MarketsOfCustomersAxis": "markets of customers", "x:HpcMember": "HPC"}.get(qname))
    [group] = result.groups
    assert group.label == "Revenue by Markets of Customers"
    assert [label for _key, label, _value in group.members] == ["HPC", "Smartphone"]


def test_overlapping_detail_rows_are_dropped_only_when_that_reconciles():
    regions = {"US & Canada": 78_866, "Europe": 46_569, "Asia Pacific": 53_817, "Rest of World": 21_714, "U.S.": 74_780}
    assert "U.S." not in drop_overlaps(regions, 200_966)
    assert drop_overlaps({"A": 60, "B": 30, "C": 5}, 100) == {"A": 60, "B": 30, "C": 5}


def test_member_keys_follow_labels_across_filings():
    assert member_key("Compute & Networking") == member_key("Compute and Networking Segment")
    assert member_key("Other countries") == member_key("All other countries not separately disclosed") == "other"
    assert member_key("Rest of World") != "other"
    assert member_key("Asia-Pacific (APAC)") == member_key("Asia-Pacific")


def test_breakdowns_without_a_reported_total_are_not_kept():
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 60.0, "x:ProductAMember"),
        _fact("us-gaap:Revenues", 40.0, "x:ProductBMember"),
    ])
    assert extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups == []


def test_rows_sharing_a_key_add_their_nine_months_once():
    from kpi_extractor.xbrl import _by_key
    assert _by_key([("other", "Other", 30.0), ("other", "All other", 60.0), ("cloud", "Cloud", 5.0)]) == {
        "other": 90.0, "cloud": 5.0}


def test_two_complete_splits_on_one_axis_keep_the_finer():
    from kpi_extractor.xbrl import _two_partitions
    members = {"product": 64.0, "service": 268.0, "server": 129.0, "office": 111.0, "gaming": 22.0, "linkedin": 20.0, "other": 50.0}
    fine, coarse = _two_partitions(members, 332.0)
    assert set(fine) == {"server", "office", "gaming", "linkedin", "other"} and set(coarse) == {"product", "service"}


def test_a_region_beside_its_countries_is_the_coarser_split():
    # Adobe's FY2025 10-K: Americas beside United States and Other Americas.
    from kpi_extractor.xbrl import _two_partitions
    members = {"us": 12529.0, "otheramericas": 1591.0, "americas": 14120.0, "emea": 6289.0, "apac": 3360.0}
    fine, coarse = _two_partitions(members, 23769.0)
    assert set(fine) == {"us", "otheramericas", "emea", "apac"} and set(coarse) == {"americas", "emea", "apac"}
    # FY2020: Americas, EMEA and Asia each beside their countries.
    members = {"americas": 7454.0, "asia": 2014.0, "emea": 3400.0, "gb": 880.0, "jp": 893.0, "otheramericas": 708.0,
               "otherasia": 1121.0, "othereurope": 2520.0, "us": 6746.0}
    fine, coarse = _two_partitions(members, 12868.0)
    assert set(coarse) == {"americas", "asia", "emea"} and len(fine) == 6


def test_products_against_services_is_published_beside_the_product_lines():
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact("us-gaap:Revenues", 40.0, "us-gaap:ProductMember"),
        _fact("us-gaap:Revenues", 60.0, "us-gaap:ServiceMember"),
        _fact("us-gaap:Revenues", 35.0, "x:OnlineStoresMember"),
        _fact("us-gaap:Revenues", 25.0, "x:AwsMember"),
        _fact("us-gaap:Revenues", 30.0, "x:AdvertisingMember"),
        _fact("us-gaap:Revenues", 10.0, "x:OtherMember"),
    ])
    groups = {group.key: group for group in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups}
    assert {key for key, _label, _value in groups["product_service"].members} == {"product", "service"}
    assert len(groups["products"].members) == 4 and groups["products"].alternative == []


def test_segments_reported_before_eliminations_add_up_with_them():
    segment = "dim_us-gaap_StatementBusinessSegmentsAxis"
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact("us-gaap:Revenues", 70.0, "x:InsuranceMember", axis=segment, consolidation="us-gaap:OperatingSegmentsMember"),
        _fact("us-gaap:Revenues", 50.0, "x:ServicesMember", axis=segment, consolidation="us-gaap:OperatingSegmentsMember"),
        _fact("us-gaap:Revenues", 45.0, "x:TotalServicesMember", axis=segment),  # net of its own internal sales
        _fact("us-gaap:Revenues", -20.0, None, axis=segment, consolidation="us-gaap:IntersegmentEliminationMember"),
    ])
    group = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups[0]
    assert {key: value for key, _label, value in group.members} == {"insurance": 70.0, "services": 50.0, "eliminations": -20.0}


def test_revenue_outside_every_split_is_kept_as_one_row_when_two_splits_agree():
    contract = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    segment, region = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_StatementGeographicalAxis"
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 99.0),  # hedging losses of 1 are in revenue but in no split
        _fact(contract, 70.0, "x:ServicesMember", axis=segment), _fact(contract, 30.0, "x:CloudMember", axis=segment),
        _fact(contract, 60.0, "country:US", axis=region), _fact(contract, 40.0, "x:EmeaMember", axis=region),
    ])
    groups = {g.key: g for g in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups}
    assert {k: v for k, _n, v in groups["segments"].members} == {"services": 70.0, "cloud": 30.0, "unallocated": -1.0}
    assert groups["geography"].reconciliation_error == 0
    lone = pd.DataFrame([_fact("us-gaap:Revenues", 99.0), _fact(contract, 70.0, "x:ServicesMember", axis=segment),
                         _fact(contract, 30.0, "x:CloudMember", axis=segment)])
    assert extract_breakdowns(lone, {"document_period_end_date": "2026-06-30"}, "10-Q").groups == []  # nothing agrees


def test_a_zero_row_stays_when_the_business_had_revenue_this_year_and_goes_when_it_never_does():
    def keys(other_to_date):
        quarter = dict(start="2026-07-01", end="2026-09-30")
        to_date = dict(start="2026-01-01", end="2026-09-30")
        rows = [_fact("us-gaap:Revenues", 100.0, **quarter), _fact("us-gaap:Revenues", 60.0, "x:GpuMember", **quarter),
                _fact("us-gaap:Revenues", 40.0, "x:TegraMember", **quarter), _fact("us-gaap:Revenues", 0.0, "x:OtherMember", **quarter),
                _fact("us-gaap:Revenues", 280.0, **to_date), _fact("us-gaap:Revenues", 160.0, "x:GpuMember", **to_date),
                _fact("us-gaap:Revenues", 120.0 - other_to_date, "x:TegraMember", **to_date),
                _fact("us-gaap:Revenues", other_to_date, "x:OtherMember", **to_date)]
        group = extract_breakdowns(pd.DataFrame(rows), {"document_period_end_date": "2026-09-30"}, "10-Q").groups[0]
        return {key for key, _label, _value in group.members}
    assert "other" in keys(43.0)  # licensing that ended after Q1: zero now, but part of the year
    assert "other" not in keys(0.0)  # never anything: not a part of revenue


def test_a_split_tagged_inside_one_segment_is_completed_by_the_other_segments():
    contract = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    segment = "dim_us-gaap_StatementBusinessSegmentsAxis"
    def line(value, product):
        row = _fact(contract, value, product)
        row[segment] = "x:ServicesMember"
        return row
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 100.0),
        _fact(contract, 80.0, "x:ServicesMember", axis=segment), _fact(contract, 20.0, "x:CloudMember", axis=segment),
        line(50.0, "x:SearchMember"), line(20.0, "x:YouTubeMember"), line(10.0, "x:NetworkMember"),
    ])
    groups = {g.key: g for g in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups}
    assert {k: v for k, _n, v in groups["products"].members} == {"search": 50.0, "youtube": 20.0, "network": 10.0, "cloud": 20.0}
    assert groups["products"].reconciliation_error == 0


def test_a_declared_parent_is_a_subtotal_even_when_it_is_not_its_childrens_sum():
    from kpi_extractor.xbrl import drop_declared_parents
    children = {"unh:TotalOptumMember": {"unh:OptumHealthMember", "unh:OptumInsightMember", "unh:OptumRxMember"}}
    members = {"unh:UnitedhealthcareMember": 86.0, "unh:OptumHealthMember": 23.5, "unh:OptumInsightMember": 5.4,
               "unh:OptumRxMember": 38.3, "unh:TotalOptumMember": 65.7}  # net of Optum's internal sales
    assert "unh:TotalOptumMember" not in drop_declared_parents(members, 112.0, children)
    # Parents that together split revenue (products against services) are a coarser split, not subtotals.
    split = {"x:ProductMember": 40.0, "x:ServiceMember": 60.0, "x:AMember": 30.0, "x:BMember": 10.0, "x:CMember": 60.0}
    tree = {"x:ProductMember": {"x:AMember", "x:BMember"}, "x:ServiceMember": {"x:CMember", "x:AMember"}}
    assert drop_declared_parents(split, 100.0, tree) == split




def test_a_two_line_split_inside_one_segment_is_completed_by_the_other_segments():
    # AMD since 2025: Data Center, Client and Gaming, Embedded as segments; Client and Gaming also tagged by product.
    def both(value, product, start="2026-04-01", end="2026-06-30"):
        return {**_fact("us-gaap:Revenues", value, "amd:ClientAndGamingMember", axis="dim_us-gaap_StatementBusinessSegmentsAxis",
                        start=start, end=end), "dim_srt_ProductOrServiceAxis": product}
    def segment(value, member, **period):
        return _fact("us-gaap:Revenues", value, member, axis="dim_us-gaap_StatementBusinessSegmentsAxis",
                     consolidation="us-gaap:OperatingSegmentsMember", **period)
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 7438.0),
        segment(3674.0, "amd:DataCenterMember"), segment(2941.0, "amd:ClientAndGamingMember"), segment(823.0, "amd:EmbeddedMember"),
        both(2294.0, "amd:ClientMember"), both(647.0, "amd:GamingMember"),
    ])
    result = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q")
    groups = {group.key: group for group in result.groups}
    assert {key: value for key, _label, value in groups["segments"].members} == {"datacenter": 3674.0, "clientandgaming": 2941.0, "embedded": 823.0}
    assert {key: value for key, _label, value in groups["products"].members} == {"datacenter": 3674.0, "client": 2294.0, "embedded": 823.0, "gaming": 647.0}
    assert result.rejected == []
    assert groups["products"].parents == {"client": "clientandgaming", "gaming": "clientandgaming"}
    # AMD's FY2025 10-K tags Gaming under a "Gaming" segment of its own: the lines still complete Client and Gaming.
    mistyped = facts.copy()
    mistyped.loc[mistyped["dim_srt_ProductOrServiceAxis"] == "amd:GamingMember", "dim_us-gaap_StatementBusinessSegmentsAxis"] = "amd:GamingMember"
    groups = {group.key: group for group in extract_breakdowns(mistyped, {"document_period_end_date": "2026-06-30"}, "10-Q").groups}
    assert {key: value for key, _label, value in groups["products"].members} == {"datacenter": 3674.0, "client": 2294.0, "embedded": 823.0, "gaming": 647.0}



def test_a_gap_the_filing_reports_as_its_own_line_takes_its_name():
    # Alphabet: segments short of revenue by its hedging gains, tagged RevenueNotFromContractWithCustomer.
    segment = lambda value, member: _fact("us-gaap:Revenues", value, member, axis="dim_us-gaap_StatementBusinessSegmentsAxis",
                                          consolidation="us-gaap:OperatingSegmentsMember")
    product = lambda value, member: _fact("us-gaap:Revenues", value, member)
    facts = pd.DataFrame([
        _fact("us-gaap:Revenues", 1000.0),
        {**_fact("us-gaap:RevenueNotFromContractWithCustomer", 10.0), "label": "Hedging gains (losses)"},
        segment(700.0, "x:ServicesMember"), segment(290.0, "x:CloudMember"),
        product(600.0, "x:SearchMember"), product(390.0, "x:OtherMember"),
    ])
    groups = {g.key: g for g in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups}
    assert ("hedginggains", "Hedging gains (losses)", 10.0) in groups["segments"].members


def test_a_split_keeps_the_filings_subtotals_as_levels_of_its_tree():
    # Alphabet's Q3 2020 10-Q: inside the Google segment, Search + YouTube = Google properties; it + Network = advertising.
    from kpi_extractor.xbrl import subtotal_tree
    rows = {"search": 26338.0, "youtube": 5037.0, "properties": 31375.0, "network": 5720.0, "advertising": 37095.0,
            "cloud": 3444.0, "other": 5478.0}
    leaves = {key: rows[key] for key in ("search", "youtube", "network", "cloud", "other")}
    parents, subtotals = subtotal_tree(rows, leaves, "google")
    assert subtotals == {"properties": 31375.0, "advertising": 37095.0}
    assert parents == {"search": "properties", "youtube": "properties", "properties": "advertising", "network": "advertising",
                       "advertising": "google", "cloud": "google", "other": "google"}


def test_regional_segments_read_as_regions_and_the_country_split_as_countries():
    from kpi_extractor.xbrl import XbrlGroup, _name_regional_views
    apple = [XbrlGroup("segments", "Revenue by Segment", 0, [("americas", "Americas", 45.8), ("europe", "Europe", 29.4),
                                                           ("greaterchina", "Greater China", 18.8), ("japan", "Japan", 6.6),
                                                           ("restofasiapacific", "Rest of Asia Pacific", 8.9)], 109.4, 0.0, "USD", "c"),
             XbrlGroup("geography", "Revenue by Geography", 2, [("us", "U.S.", 40.0), ("china", "China", 18.8),
                                                               ("othercountries", "Other countries", 50.6)], 109.4, 0.0, "USD", "c")]
    _name_regional_views(apple)
    assert [group.label for group in apple] == ["Revenue by Region", "Revenue by Country"]
    # Amazon's segments mix a business (AWS) with regions: still segments.
    amazon = [XbrlGroup("segments", "Revenue by Segment", 0, [("northamerica", "North America", 100.0), ("international", "International", 40.0),
                                                            ("aws", "AWS", 30.0)], 170.0, 0.0, "USD", "c")]
    _name_regional_views(amazon)
    assert amazon[0].label == "Revenue by Segment"


def test_every_segment_with_eliminations_beats_a_subset_that_comes_close():
    # Intel's Q3 2025 10-Q: Foundry's revenue is nearly all intersegment, so the other segments alone come within 0.1%.
    import pandas as pd
    from kpi_extractor.xbrl import extract_breakdowns
    start, end = "2025-06-29", "2025-09-27"
    def fact(value, member=None, consolidation=None):
        return {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "numeric_value": value,
                "period_type": "duration", "period_start": start, "period_end": end, "unit_ref": "usd",
                "dim_us-gaap_StatementBusinessSegmentsAxis": member, "dim_srt_ConsolidationItemsAxis": consolidation}
    rows = [fact(8_535e6, "intc:ClientComputingGroupMember", "us-gaap:OperatingSegmentsMember"),
            fact(4_235e6, "intc:IntelFoundryMember", "us-gaap:OperatingSegmentsMember"),
            fact(4_117e6, "intc:DatacenterAndAIMember", "us-gaap:OperatingSegmentsMember"),
            fact(993e6, "us-gaap:AllOtherSegmentsMember", "us-gaap:OperatingSegmentsMember"),
            fact(-4_227e6, None, "us-gaap:IntersegmentEliminationMember"), fact(13_653e6)]
    breakdowns = extract_breakdowns(pd.DataFrame(rows), {"document_period_end_date": end, "fiscal_period": "Q3",
                                                          "fiscal_year": 2025}, "10-Q", lambda _q: None, {})
    segments = next(group for group in breakdowns.groups if group.key == "segments")
    assert len(segments.members) == 5 and segments.reconciliation_error == 0.0


def test_a_line_tagged_only_beside_its_segment_completes_the_split():
    # Intel's Q2 2021 10-Q tags "DCG platform" only with its Data Center Group; the other platforms alone.
    import pandas as pd
    from kpi_extractor.xbrl import extract_breakdowns
    def fact(value, product=None, segment=None):
        return {"concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "numeric_value": value,
                "period_type": "duration", "period_start": "2021-03-28", "period_end": "2021-07-03", "unit_ref": "usd",
                "dim_srt_ProductOrServiceAxis": product, "dim_us-gaap_StatementBusinessSegmentsAxis": segment}
    rows = [fact(15_991e6, "intc:PlatformMember"), fact(3_640e6, "srt:OtherProductOrServiceMember"),
            fact(2_635e6, "intc:DesktopPlatformMember"), fact(6_743e6, "intc:NotebookPlatformMember"),
            fact(19e6, "intc:OtherPlatformMember"), fact(891e6, "intc:IOTGPlatformMember"),
            fact(5_703e6, "intc:DCGPlatformMember", "intc:DataCenterGroupMember"),
            fact(9_397e6, "intc:PlatformMember", "intc:ClientComputingGroupMember"),
            fact(712e6, "srt:OtherProductOrServiceMember", "intc:ClientComputingGroupMember"),
            fact(752e6, "srt:OtherProductOrServiceMember", "intc:DataCenterGroupMember"), fact(19_631e6)]
    breakdowns = extract_breakdowns(pd.DataFrame(rows), {"document_period_end_date": "2021-07-03", "fiscal_period": "Q2",
                                                          "fiscal_year": 2021}, "10-Q", lambda _q: None, {})
    products = next(group for group in breakdowns.groups if group.key == "products")
    assert len(products.members) == 6 and products.reconciliation_error == 0.0


def _kkr_fact(concept, value, **dims):
    return {"concept": concept, "period_type": "duration", "period_start": "2026-04-01", "period_end": "2026-06-30",
            "numeric_value": value, "currency": "USD", "unit_ref": "USD", **{f"dim_{axis}": member for axis, member in dims.items()}}


def test_segment_level_lines_beside_the_consolidated_ones_are_another_table():
    # KKR's 2018 10-Q: its fees consolidated, and again by segment ("operating segments"), sharing members; the
    # consolidated fees add up to their own figure, the segment ones to the segments' total.
    product, segments = "srt_ProductOrServiceAxis", "srt_ConsolidationItemsAxis"
    concept = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    facts = pd.DataFrame([
        _kkr_fact(concept, 100.0),
        _kkr_fact(concept, 60.0, **{product: "kkr:ManagementMember"}), _kkr_fact(concept, 40.0, **{product: "kkr:TransactionMember"}),
        _kkr_fact(concept, 90.0, **{product: "kkr:ManagementMember", segments: "us-gaap:OperatingSegmentsMember"}),
        _kkr_fact(concept, 30.0, **{product: "kkr:CreditsMember", segments: "us-gaap:OperatingSegmentsMember"}),
    ])
    group = next(g for g in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups if g.key == "products")
    assert {key: value for key, _label, value in group.members} == {"management": 60.0, "transaction": 40.0}


def test_a_companys_own_axis_tagged_operating_segments_is_its_segments():
    # KKR's 2018 business lines: segment revenues on its own axis, beside GAAP revenue the segments do not add up to.
    line, segments = "kkr_BusinessLineAxis", "srt_ConsolidationItemsAxis"
    facts = pd.DataFrame([
        _kkr_fact("us-gaap:Revenues", 971.0),
        _kkr_fact("us-gaap:Revenues", 926.0, **{segments: "us-gaap:OperatingSegmentsMember"}),
        *(_kkr_fact("us-gaap:Revenues", value, **{line: member, segments: "us-gaap:OperatingSegmentsMember"})
          for member, value in (("kkr:PrivateMarketsBusinessLineMember", 529.0), ("kkr:PublicMarketsBusinessLineMember", 124.0),
                                ("kkr:CapitalMarketsBusinessLineMember", 105.0), ("kkr:PrincipalActivitiesBusinessLineMember", 168.0))),
    ])
    labels = {"kkr:BusinessLineAxis": "Business Line"}
    group = extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q", labels.get).groups[0]
    assert group.key == "segments" and group.total == 926.0
    assert {label for _key, label, _value in group.members} == {"Private Markets", "Public Markets", "Capital Markets", "Principal Activities"}


def test_lines_adding_up_to_one_segments_figure_are_a_split_of_it():
    # KKR since 2021: its fee lines add up to its Asset Management segment's Fees and Other, beside segment-level
    # figures under another member, and the insurer's revenue makes up the rest of the company's.
    product, segment, items = "srt_ProductOrServiceAxis", "us-gaap_StatementBusinessSegmentsAxis", "srt_ConsolidationItemsAxis"
    concept = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    facts = pd.DataFrame([
        _kkr_fact("us-gaap:Revenues", 5_726.0),
        _kkr_fact("us-gaap:Revenues", 3_523.0, **{segment: "kkr:InsuranceSegmentMember"}),
        _kkr_fact("us-gaap:Revenues", 2_203.0, **{segment: "kkr:AssetManagementAndStrategicHoldingsSegmentsMember"}),
        _kkr_fact(concept, 1_180.0, **{segment: "kkr:AssetManagementAndStrategicHoldingsSegmentsMember"}),
        *(_kkr_fact(concept, value, **{segment: "kkr:AssetManagementAndStrategicHoldingsSegmentsMember", product: member})
          for member, value in (("kkr:AssetManagement1Member", 830.0), ("kkr:TransactionFeeMember", 371.0), ("kkr:FeeCreditMember", -21.0))),
        _kkr_fact(concept, 1_250.0, **{segment: "kkr:AssetManagementSegmentMember", product: "kkr:ManagementFeesMember",
                                       items: "us-gaap:OperatingSegmentsMember"}),
    ])
    group = next(g for g in extract_breakdowns(facts, {"document_period_end_date": "2026-06-30"}, "10-Q").groups if g.key == "products")
    assert group.total == 1_180.0 and sum(value for *_rest, value in group.members) == 1_180.0
