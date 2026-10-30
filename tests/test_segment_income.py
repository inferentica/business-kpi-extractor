"""Segment operating income as Alphabet and Nvidia tag it in their Q3 2026 / Q2 FY2027 10-Qs."""
from datetime import date

import pandas as pd

from kpi_extractor.segment_income import segment_income

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
