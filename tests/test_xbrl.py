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
