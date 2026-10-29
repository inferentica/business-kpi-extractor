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
