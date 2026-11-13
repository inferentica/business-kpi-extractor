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


def test_a_split_tagged_alone_beats_one_completed_with_a_fixed_members_siblings():
    # Adobe's 2024 Q2 10-Q tags its three segments alone ($3,908 + $1,327 + $74 million) and its subscription revenue by
    # segment ($3,828, $1,204, $28 million) beside Product and Services: the joined split has five lines and still adds
    # up, but its "Digital Media" is subscription only. The segments are the ones tagged alone.
    import json
    from pathlib import Path
    from kpi_extractor.xbrl.breakdowns import extract_breakdowns
    saved = json.loads((Path(__file__).parent.parent / "fixtures" / "xbrl_revenue_adbe_q2_2024.json").read_text())
    found = extract_breakdowns(pd.DataFrame(saved["facts"]), saved["entity"], "10-Q")
    segments = next(group for group in found.groups if group.key == "segments")
    assert [(key, value) for key, _name, value in segments.members] == [
        ("digitalmedia", 3_908e6), ("digitalexperience", 1_327e6), ("publishingandadvertising", 74e6)]
