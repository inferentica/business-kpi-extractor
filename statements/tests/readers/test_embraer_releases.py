"""Embraer's releases, 2011-25 (round 91)."""
from datetime import date

from statement_extractor.domain.printed_labels import NET_INCOME
from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.dates import column_periods


def row(*cells):
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def test_note_marks_over_the_headings_and_a_comma_after_the_month():
    # Q1 2017: "(1) | (1) | (1)" over "Three months ended on" over "31 Dec, 2016 | 31 Mar, 2016 | 31 Mar, 2017".
    html = ("<p>10 FINANCial statements</p><table>" + row("EMBRAER S.A.", "", "", "") + row("CONSOLIDATED STATEMENTS OF INCOME", "", "", "")
            + row("(in millions of U.S.dollars, except earnings per share)", "", "", "") + row("", "(1)", "(1)", "(1)")
            + "<tr><td></td><td colspan='3'>Three months ended on</td></tr>" + row("", "31 Dec, 2016", "31 Mar, 2016", "31 Mar, 2017")
            + row("Revenue", "2,027.8", "1,309.0", "1,026.3") + row("Cost of sales and services", "(1,700.0)", "(1,050.0)", "(840.0)")
            + row("Gross profit", "327.8", "259.0", "186.3") + "</table>")
    table = parse_html(html)[0]
    assert [row.label for row in table.rows if row.figures][0] == "Revenue"
    assert sorted((p.start, p.end) for p in column_periods("income", [table], "1231", date(2017, 3, 31)).values()) == [
        (date(2016, 1, 1), date(2016, 3, 31)), (date(2016, 10, 1), date(2016, 12, 31)), (date(2017, 1, 1), date(2017, 3, 31))]


def test_dotted_thousands_with_a_decimal_comma():
    from statement_extractor.readers.html.cells import dotted_thousands
    from statement_extractor.readers.printed.tables import Cell
    lines = [[Cell(0, 1, "REVENUE"), Cell(1, 1, "1.044,0"), Cell(2, 1, "363,6")]]
    assert [cell.text for cell in dotted_thousands(lines)[0]] == ["REVENUE", "1,044.0", "363.6"]


def test_a_misprinted_span_heading_repeating_the_quarter():
    # Q3 2011's cash flows head the nine months "Three months ended on" too, beside the quarter.
    from statement_extractor.readers.printed.ranges import without_repeats
    html = ("<p>IFRS CONSOLIDATED STATEMENTS OF CASH FLOWS (in million of U.S. dollars)</p><table>"
            + "<tr><td></td><td colspan='2'>Three months ended on</td><td colspan='2'>Three months ended on</td></tr>"
            + row("", "September 30, 2010", "September 30, 2011", "September 30, 2010", "September 30, 2011")
            + row("Net income", "127.4", "4.3", "215.8", "209.0") + row("Depreciation", "33.3", "29.2", "73.0", "88.9") + "</table>")
    table = parse_html(html)[0]
    periods = without_repeats(table, column_periods("cash_flow", [table], "1231", date(2011, 9, 30)))
    assert sorted(periods) == [(1,), (2,)]


def test_loss_and_income_for_the_period():
    assert NET_INCOME.search("LOSS FOR THE PERIOD") and NET_INCOME.search("Losses for the period") and NET_INCOME.search("INCOME FOR THE PERIOD")
    assert not NET_INCOME.search("Other comprehensive income for the period")


def test_the_nearest_subtotal_within_rounding():
    from statement_extractor.checks.subtotals import subtotals
    # June 2011's non-current assets: without "Trade accounts receivable 0.5" the sum is within rounding of ten lines.
    printed = [("Non-current assets", 0, None)] + [(f"line {n}", 0, value) for n, value in enumerate(
        (5, 157, 492, 450, 5297, 49, 2657, 4651, 1147, 13361, 8320))] + [("", 0, 36586)]
    assert subtotals(printed)[len(printed) - 1] == 1
