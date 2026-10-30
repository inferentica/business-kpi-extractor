"""Segment and product revenue read from Alphabet's Q3 2026 earnings release (Exhibit 99.1) before its 10-Q."""
from datetime import date

from kpi_extractor.document import Document, Table
from kpi_extractor.release_breakdowns import line_key, release_breakdown

CONTEXT = "(in millions, except for number of employees; unaudited) Revenues, Traffic Acquisition Costs (TAC), and Number of Employees"
REVENUES = Table("T1", CONTEXT, [
    ["", "Quarter Ended September 30,", ""],
    ["", "2025", "2026"],
    ["Google Search & other", "$56,567", "$63,350"],
    ["YouTube ads", "10,261", "11,207"],
    ["Google Network", "7,354", "7,455"],
    ["Google advertising", "74,182", "82,012"],
    ["Google subscriptions, platforms, and devices", "12,870", "14,290"],
    ["Google Services total", "87,052", "96,302"],
    ["Google Cloud", "15,157", "30,709"],
    ["Other Bets", "344", "372"],
    ["Hedging gains (losses)", "(207)", "194"],
    ["Total revenues", "$102,346", "$127,577"],
    ["Total TAC", "$14,876", "$16,366"],
    ["Number of employees", "190,167", "204,757"],
])
OPERATING = Table("T2", f"{CONTEXT} Segment Operating Results", [
    ["", "Quarter Ended September 30,", ""],
    ["", "2025", "2026"],
    ["Operating income (loss):", "", ""],
    ["Google Services", "$33,527", "$39,157"],
    ["Google Cloud", "3,594", "11,121"],
    ["Other Bets", "(1,426)", "(2,047)"],
    ["Alphabet-level activities(1)", "(4,467)", "(7,655)"],
    ["Total income from operations", "$31,228", "$40,576"],
])
DOCUMENT = Document("https://example.test/googexhibit991q32026.htm", {"T2": OPERATING, "T1": REVENUES}, {}, ["T2", "T1"])
END = date(2026, 9, 30)
M = 1e6

SEGMENTS = {"googleservices": "Google Services", "googlecloud": "Google Cloud", "otherbets": "Other Bets",
            "hedginggains": "Hedging gains (losses)"}
SEGMENTS_YEAR_AGO = {"googleservices": 87_052 * M, "googlecloud": 15_157 * M, "otherbets": 344 * M, "hedginggains": -207 * M}


def test_line_keys_match_the_reports_series():
    assert line_key("Google Services total") == "googleservices"
    assert line_key("Google Search & other") == "googlesearchandother"
    assert line_key("Hedging gains (losses)") == "hedginggains"
    assert line_key("Alphabet-level activities(1)") == "alphabetlevelactivities"


def test_segments_come_from_the_revenue_table_not_operating_income():
    found = release_breakdown(DOCUMENT, SEGMENTS, SEGMENTS_YEAR_AGO, END)
    assert found is not None and found.table == "T1"
    assert found.values == {"googleservices": 96_302 * M, "googlecloud": 30_709 * M, "otherbets": 372 * M,
                            "hedginggains": 194 * M}
    assert found.total == 127_577 * M
    assert found.rows["googleservices"] == "Google Services total"


def test_product_lines_and_their_subtotal():
    products = {"googlesearchandother": "Google Search & other", "youtubeads": "YouTube ads", "googlenetwork": "Google Network",
                "googlesubscriptionsplatformsanddevices": "Google subscriptions, platforms, and devices",
                "googlecloud": "Google Cloud", "otherbets": "Other Bets", "hedginggains": "Hedging gains (losses)"}
    year_ago = {"googlesearchandother": 56_567 * M, "youtubeads": 10_261 * M, "googlecloud": 15_157 * M}
    found = release_breakdown(DOCUMENT, products, year_ago, END)
    assert found is not None and sum(found.values.values()) == found.total
    levels = release_breakdown(DOCUMENT, {"googleadvertising": "Google advertising"}, {}, END, tables=[found.table],
                               totalled=False)
    assert levels is not None and levels.values["googleadvertising"] == 82_012 * M
    assert release_breakdown(DOCUMENT, {"googleadvertising": "Google advertising"}, {}, END) is None  # no column proven


def test_a_restated_year_ago_or_a_missing_line_is_left_for_the_report():
    assert release_breakdown(DOCUMENT, SEGMENTS, {**SEGMENTS_YEAR_AGO, "googlecloud": 15_000 * M}, END) is None
    assert release_breakdown(DOCUMENT, {**SEGMENTS, "unitedstates": "United States"}, SEGMENTS_YEAR_AGO, END) is None
    assert release_breakdown(DOCUMENT, SEGMENTS, SEGMENTS_YEAR_AGO, date(2026, 6, 30)) is None  # another quarter's table


def test_a_segment_table_laid_out_by_heading_gives_revenue_and_operating_income():
    # Amazon's Q3 2026 release: each segment a heading over its net sales and operating income.
    from kpi_extractor.document import Table
    rows = [["", "Three Months Ended September 30,", "", "Nine Months Ended September 30,", ""], ["", "2025", "2026", "2025", "2026"]]
    for name, sales, income in (("North America", ("$106,267", "$112,852"), ("$4,789", "$8,723")), ("International", ("$40,896", "$43,108"), ("$1,199", "$1,580")),
                                ("AWS", ("$33,006", "$45,984"), ("$11,434", "$17,773")), ("Consolidated", ("$180,169", "$201,944"), ("$17,422", "$28,076"))):
        rows += [[name, "", "", "", ""], ["Net sales", *sales, "", ""], ["Operating expenses", "1", "1", "", ""], ["Operating income", *income, "", ""]]
    table = Table("T3", "AMAZON.COM, INC. Segment Information (in millions) (unaudited)", rows)
    document = Document("x", {"T3": table}, {}, ["T3"])
    members = {"northamerica": "North America", "international": "International", "aws": "AWS"}
    revenue = release_breakdown(document, members, {"northamerica": 106_267e6, "aws": 33_006e6}, date(2026, 9, 30))
    assert revenue.values == {"northamerica": 112_852e6, "international": 43_108e6, "aws": 45_984e6} and revenue.total == 201_944e6
    income = release_breakdown(document, members, {"northamerica": 4_789e6, "aws": 11_434e6}, date(2026, 9, 30), measure="income")
    assert income.values == {"northamerica": 8_723e6, "international": 1_580e6, "aws": 17_773e6} and income.total == 28_076e6


def test_a_segment_table_laid_out_by_column_with_its_year_ago_table():
    # Intel's Q3 2026 release: segments across, revenue and operating income down, one table per quarter.
    def quarter(table_id, day, revenue, income):
        rows = [["", "Three Months Ended", "", "", "", "", "", ""], ["(In Millions; Unaudited)", day, "", "", "", "", "", ""],
                ["", "CCPG1", "DCAI", "Total Intel Products", "Intel Foundry", "All Other2", "Intersegment Eliminations", "Total Consolidated"],
                ["Revenue", *revenue], ["Cost of sales and operating expenses", "1", "1", "1", "1", "1", "1", "1"],
                ["Operating income (loss)", *income, ""]]
        return Table(table_id, "Intel Corporation Supplemental Operating Segment Results", rows)
    now = quarter("T7", "Sep 26, 2026", ["$8,097", "$7,318", "$15,415", "$6,697", "$692", "$(6,291)", "$16,513"],
                  ["$2,285", "$3,039", "$5,324", "$(1,853)", "$179", "$(70)"])
    now.rows[-1][-1] = "$2,522"
    before = quarter("T8", "Sep 27, 2025", ["$8,535", "$4,117", "$12,652", "$4,235", "$993", "$(4,227)", "$13,653"],
                     ["$2,694", "$964", "$3,658", "$(2,321)", "$100", "$28"])
    before.rows[-1][-1] = "$683"
    document = Document("x", {"T7": now, "T8": before}, {}, ["T7", "T8"])
    segments = {"ccpg": "CCPG", "dcai": "DCAI", "intel_foundry": "Intel Foundry", "all_other": "All Other",
                "intersegment_eliminations": "Intersegment Eliminations"}
    revenue = release_breakdown(document, segments, {"dcai": 4_117e6, "intel_foundry": 4_235e6}, date(2026, 9, 26))
    assert revenue.table == "T7" and revenue.values["ccpg"] == 8_097e6 and revenue.total == 16_513e6
    income = release_breakdown(document, {"ccpg": "CCPG", "dcai": "DCAI", "all_other": "All Other", "intel_foundry": "Intel Foundry",
                                          "unallocated": "Unallocated"},
                               {"dcai": 964e6, "unallocated": -754e6}, date(2026, 9, 27), measure="income")
    assert income.values["unallocated"] == -1_128e6 and income.total == 2_522e6
