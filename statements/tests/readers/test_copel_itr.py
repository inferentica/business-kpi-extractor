"""Copel's 2007-10 quarterly information and releases, unread until round 84."""
from datetime import date

from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.dates import column_periods
from statement_extractor.readers.printed.scales import statement_scale
from statement_extractor.readers.printed.tables import figure_columns


def row(*items):
    return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"


_HEAD = (row("CODE", "DESCRIPTION", "N.", "Parent Company", "", "Consolidated", "") + row("", "", "no.", "", "", "", "")
         + row("", "", "", "{}", "{}", "{}", "{}"))


def test_a_note_column_headed_n_and_figures_in_dotted_thousands():
    # Copel's Q3 2008 ITR: "N." over "no." heads the note numbers, and figures print "9.520.238".
    html = ("<p>Balance Sheet - Assets As of September 30, 2008 and June 30, 2008 (In thousands of reais )</p><table>"
            + _HEAD.format("30/09/2008", "30/06/2008", "30/09/2008", "30/06/2008") + "".join(row(*line) for line in (
                ("1", "TOTAL ASSETS", "", "9.520.238", "9.349.151", "13.083.881", "12.734.862"),
                ("1.01", "CURRENT ASSETS", "", "639.406", "588.472", "3.611.009", "3.300.555"),
                ("1.01.01", "Cash in hand", "3", "374.792", "497.198", "1.245.551", "1.200.003"),
                ("1.01.02", "Receivables", "", "264.614", "91.274", "2.365.458", "2.100.552"))) + "</table>")
    table = parse_html(html)[0]
    assert [row.figures for row in table.rows if row.label == "TOTAL ASSETS"] == [
        {3: 9_520_238, 4: 9_349_151, 5: 13_083_881, 6: 12_734_862}]
    assert figure_columns(table) == [(3,), (4,), (5,), (6,)]  # never the note numbers' column
    assert [period.end for period in column_periods("balance", [table], "1231", date(2008, 9, 30)).values()] == [
        date(2008, 9, 30), date(2008, 6, 30), date(2008, 9, 30), date(2008, 6, 30)]


def test_commas_for_thousands_keep_dots_as_decimals():
    from statement_extractor.readers.html.cells import dotted_thousands
    from statement_extractor.readers.printed.tables import Cell
    lines = [[Cell(0, 1, "Total"), Cell(1, 1, "1,234,567"), Cell(2, 1, "1.234.567")]]
    assert dotted_thousands(lines) == lines  # mixed: left as printed
    lines = [[Cell(0, 1, "Earnings per share"), Cell(1, 1, "0,99"), Cell(2, 1, "1.234.567"), Cell(3, 1, "30.09.2008")]]
    assert [cell.text for cell in dotted_thousands(lines)[0]] == ["Earnings per share", "0.99", "1,234,567", "30.09.2008"]
    lines = [[Cell(0, 1, "Cash"), Cell(1, 1, "639.406"), Cell(2, 1, "1.5")]]
    assert dotted_thousands(lines) == lines  # no figure only thousands read so: as printed


def test_a_flow_column_dated_against_its_title_is_left_out():
    # Copel's Q1 2010 ITR prints "31/12/2009" over the first quarter of 2009, under "As of March 31, 2010 and March 31, 2009".
    html = ("<p>Statement of Operations As of March 31, 2010 and March 31, 2009 (In thousands of reais , except net income "
            "per share)</p><table>" + _HEAD.format("31/03/2010", "31/12/2009", "31/03/2010", "31/12/2009")
            + "".join(row(*line) for line in (
                ("3.01", "GROSS REVENUES FROM SALES AND/OR SERVICES", "28", "0", "0", "2,344,556", "2,077,541"),
                ("3.03", "NET REVENUES FROM SALES AND/OR SERVICES", "", "0", "0", "1,561,422", "1,356,629"),
                ("3.15", "NET INCOME FOR THE PERIOD", "", "223,981", "272,071", "223,981", "272,071"))) + "</table>")
    periods = column_periods("income", [parse_html(html)[0]], "1231", date(2010, 3, 31))
    assert sorted((column, period.start, period.end) for column, period in periods.items()) == [
        ((3,), date(2010, 1, 1), date(2010, 3, 31)), ((5,), date(2010, 1, 1), date(2010, 3, 31))]


def test_a_releases_quarters_with_note_marks_and_its_rs_thousands():
    # Copel's 1Q09 release: "R$'000" over "INCOME STATEMENT | 1Q09 (1) | 4Q08 (2) | 1Q08 (3) | Var.% (1/2) | Var.% (1/3)".
    spaced = lambda label, *cells: row(label, *(part for cell in cells for part in ("", cell)))  # noqa: E731 as laid out
    html = ("<p>8.3 Income Statement:</p><table>" + row("R$'000", "", "", "", "", "")
            + spaced("INCOME STATEMENT", "1Q09 (1)", "4Q08 (2)", "1Q08 (3)", "Var.% (1/2)", "Var.% (1/3)")
            + "".join(spaced(*line) for line in (("Operating revenues", "2,077,541", "2,159,757", "1,989,579", "(3.8)", "4.4"),
                                              ("Net operating revenues", "1,356,629", "1,402,818", "1,314,558", "(3.3)", "3.2"),
                                              ("Net income", "272,071", "254,060", "255,512", "7.1", "6.5"))) + "</table>")
    table = parse_html(html)[0]
    assert statement_scale([table]) == 1e3
    assert [[cell.text for cell in cells] for cells in table.header][-1][1:4] == ["1Q09 (1)", "4Q08 (2)", "1Q08 (3)"]
    periods = column_periods("income", [table], "1231", date(2009, 3, 31))
    assert sorted((period.start, period.end) for period in periods.values()) == [
        (date(2008, 1, 1), date(2008, 3, 31)), (date(2008, 10, 1), date(2008, 12, 31)), (date(2009, 1, 1), date(2009, 3, 31))]
