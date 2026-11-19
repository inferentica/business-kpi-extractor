"""rules.period: a column's header is the one the table restates nearest above the row."""
from datetime import date

from kpi_extractor.documents.document import Table
from kpi_extractor.rules.period import column_dates, header_contradicts

# Caterpillar's Q3 2026 release: a sales bridge and the sales by application in one table.
CAT = Table("B_T6", "", [
    ["", "POWER & ENERGY", "", "", ""], ["(Millions of dollars)", "", "", "", ""], ["", "Segment Sales", "", "", ""],
    ["", "Third Quarter 2025", "Sales Volume", "Price Realization", "Third Quarter 2026"],
    ["Total Sales", "$7,463", "$879", "$216", "$8,784"],
    ["", "Sales by Application", "", "", ""], ["", "Third Quarter 2026", "Third Quarter 2025", "$Change", "%Change"],
    ["Power Generation", "$3,421", "$2,634", "$787", "30%"], ["Oil and Gas", "2,236", "1,979", "257", "13%"],
    ["Industrial", "1,631", "1,568", "63", "4%"]])
END = date(2026, 9, 30)


def test_the_header_restated_above_a_row_is_its_columns_own():
    assert column_dates(CAT, 7, 1) == ([], [2026]) and column_dates(CAT, 9, 1) == ([], [2026])
    assert header_contradicts(CAT, 9, 1, END) is None
    assert header_contradicts(CAT, 8, 2, END) == "column headed 2025, not the quarter ended 2026-09-30"
    # The bridge's own row reads the table's top header.
    assert header_contradicts(CAT, 4, 1, END) == "column headed 2025, not the quarter ended 2026-09-30"
    assert header_contradicts(CAT, 4, 4, END) is None


def test_a_section_label_with_no_period_leaves_the_tables_own_header():
    table = Table("T1", "", [["", "Three Months Ended September 30, 2026", "Three Months Ended September 30, 2025"],
                             ["Loans", "100", "90"], ["Consumer", "", ""], ["Cards", "12", "11"]])
    assert column_dates(table, 3, 1) == ([date(2026, 9, 30)], [2026])
    assert header_contradicts(table, 3, 2, END) == "column headed 2025-09-30, not the quarter ended 2026-09-30"
    # A header of bare years is a header, not figures.
    years = Table("T2", "", [["", "Q3", ""], ["", "2026", "2025"], ["Revenue", "5,000", "4,000"]])
    assert column_dates(years, 2, 2) == ([], [2025])
