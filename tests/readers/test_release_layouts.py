"""Release tables headed by fiscal quarters and by dates, and a layout new this year (NVIDIA's Q3 FY2027 release)."""
from datetime import date

from kpi_extractor.documents.document import Document, Table
from kpi_extractor.readers.release_breakdowns import release_breakdown

END = date(2026, 10, 25)
SEGMENTS = Table("C_T2", "CFO Commentary on Third Quarter Fiscal 2027 Results", [
    ["", "Revenue by Reportable Segments", "", "", "", ""],
    ["($ in billions)", "Q3 FY27", "Q2 FY27", "Q3 FY26", "Q/Q*", "Y/Y*"],
    ["Compute & Networking", "$106.0", "$88.3", "$50.9", "20%", "108%"],
    ["Graphics", "9.8", "7.9", "6.1", "25%", "62%"],
    ["Total", "$115.8", "$96.2", "$57.0", "20%", "103%"],
])
PLATFORMS = Table("C_T3", "CFO Commentary on Third Quarter Fiscal 2027 Results", [
    ["", "Revenue by Market Platform", "", "", "", ""],
    ["($ in billions)", "Q3 FY27", "Q2 FY27", "Q3 FY26", "Q/Q*", "Y/Y*"],
    ["Data Center", "$107.6", "$89.0", "$51.2", "21%", "110%"],
    ["Hyperscale", "60.1", "48.7", "35.1", "23%", "71%"],
    ["AI Clouds, Industrial, & Enterprise", "47.5", "40.3", "16.1", "18%", "195%"],
    ["Edge Computing", "8.2", "7.2", "5.8", "14%", "42%"],
    ["Total", "$115.8", "$96.2", "$57.0", "20%", "103%"],
])
GEOGRAPHY = Table("S_T7", "the location of the headquarters of direct customers.", [
    ["", "Three Months Ended", "", "Nine Months Ended", ""],
    ["", "Oct 25, 2026", "Oct 26, 2025", "Oct 25, 2026", "Oct 26, 2025"],
    ["", "(In millions)", "", "", ""],
    ["Geographic Revenue based upon Customer Headquarters Location:", "", "", "", ""],
    ["United States", "$72,899", "$39,177", "$196,742", "$97,759"],
    ["Taiwan", "30,295", "13,751", "69,286", "30,301"],
    ["China (including Hong Kong)", "11,050", "2,973", "23,480", "16,617"],
    ["Other", "1,600", "1,105", "4,173", "3,134"],
    ["Total revenue", "$115,844", "$57,006", "$293,681", "$147,811"],
])


def document(*tables):
    found = Document(source_url="https://www.sec.gov/x/q3fy27.htm")
    found.tables = {table.id: table for table in tables}
    return found


def test_fiscal_quarter_columns_take_the_quarter_beside_its_year_ago():
    found = release_breakdown(document(SEGMENTS), {"computeandnetworking": "Compute & Networking", "graphics": "Graphics"},
                              {"computeandnetworking": 50_908e6, "graphics": 6_098e6}, END)
    assert found is not None and found.values == {"computeandnetworking": 106_000e6, "graphics": 9_800e6}
    # The printed $0.1 billion is the rounding allowed: a year-ago figure $0.2 billion away is another quarter's.
    assert release_breakdown(document(SEGMENTS), {"computeandnetworking": "Compute & Networking", "graphics": "Graphics"},
                             {"computeandnetworking": 50_700e6, "graphics": 6_098e6}, END) is None


def test_date_columns_skip_the_nine_months():
    members = {"unitedstates": "United States", "taiwan": "Taiwan", "china": "China (including Hong Kong)", "other": "Other"}
    found = release_breakdown(document(GEOGRAPHY), members, {"unitedstates": 39_177e6, "taiwan": 13_751e6,
                                                             "china": 2_973e6, "other": 1_105e6}, END)
    assert found is not None and found.values["unitedstates"] == 72_899e6 and found.total == 115_844e6


def test_a_new_layout_stands_on_the_year_ago_revenue():
    members = {"hyperscale": "Hyperscale", "aiclouds": "AI Clouds, Industrial, & Enterprise", "edge": "Edge Computing"}
    found = release_breakdown(document(PLATFORMS), members, {}, END, year_ago_total=57_006e6)
    assert found is not None and found.values == {"hyperscale": 60_100e6, "aiclouds": 47_500e6, "edge": 8_200e6}
    assert release_breakdown(document(PLATFORMS), members, {}, END) is None  # nothing to prove it by
    assert release_breakdown(document(PLATFORMS), members, {}, END, year_ago_total=61_000e6) is None


def test_the_year_ago_revenue_is_a_total_two_breakdowns_agree_on():
    from kpi_extractor.steps.release_tables import _agreed_total
    past = {("segments", "cn"): 50_908e6, ("segments", "graphics"): 6_098e6, ("geography", "us"): 39_177e6,
            ("geography", "rest"): 17_829e6, ("segment_income", "cn"): 32_000e6}
    assert _agreed_total(past) == 57_006e6
    assert _agreed_total({("segments", "cn"): 50_908e6, ("geography", "us"): 39_177e6}) is None
