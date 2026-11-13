"""One axis's members from a period's facts: a member's own figure, tagged with the axis alone, comes first."""
import pandas as pd

from kpi_extractor.xbrl.breakdowns import _single_axis_members

PRODUCT, SEGMENT = "dim_srt_ProductOrServiceAxis", "dim_us-gaap_StatementBusinessSegmentsAxis"


def test_a_members_own_figure_wins_over_its_share_tagged_with_another_axis():
    # Exxon's 2023 Q3 10-Q, nine months of 2022: "Sales and other operating revenue" for Corporate and Financing ($19
    # million) is listed before the line's own figure ($305,511 million), which alone is the line's.
    facts = pd.DataFrame([
        {"concept": "us-gaap:Revenues", "numeric_value": 19e6, PRODUCT: "xom:SalesAndOtherOperatingRevenueMember",
         SEGMENT: "xom:CorporateAndFinancingMember"},
        {"concept": "us-gaap:Revenues", "numeric_value": 305_511e6, PRODUCT: "xom:SalesAndOtherOperatingRevenueMember", SEGMENT: None},
        {"concept": "us-gaap:Revenues", "numeric_value": 10_858e6, PRODUCT: "xom:IncomeFromEquityAffiliatesMember", SEGMENT: None},
    ])
    fixed = ("StatementBusinessSegmentsAxis", "xom:CorporateAndFinancingMember")
    members = _single_axis_members(facts, "us-gaap:Revenues", "ProductOrServiceAxis", [PRODUCT, SEGMENT], fixed)
    assert members == {"xom:SalesAndOtherOperatingRevenueMember": 305_511e6, "xom:IncomeFromEquityAffiliatesMember": 10_858e6}
    # Where every fact carries the fixed member, its figures stand, as before.
    only_fixed = facts.iloc[[0]]
    assert _single_axis_members(only_fixed, "us-gaap:Revenues", "ProductOrServiceAxis", [PRODUCT, SEGMENT], fixed) == \
        {"xom:SalesAndOtherOperatingRevenueMember": 19e6}
