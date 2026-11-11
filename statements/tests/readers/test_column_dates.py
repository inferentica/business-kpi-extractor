from datetime import date

from statement_extractor.readers.html.read import read_document


def row(*items):
    return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"


def balance(header: str) -> str:
    return ("<p>CONDENSED CONSOLIDATED BALANCE SHEET (UNAUDITED)</p><table>" + header
            + row("Cash and cash equivalents", "", "", "", "30", "", "", "", "20")
            + row("Investments", "", "", "", "70", "", "", "", "60")
            + row("Total assets", "", "", "", "100", "", "", "", "80")
            + row("Accounts payable", "", "", "", "40", "", "", "", "35")
            + row("Total equity", "", "", "", "60", "", "", "", "45")
            + row("Total liabilities and equity", "", "", "", "100", "", "", "", "80") + "</table>")


def ends(html: str, document_end: date) -> list[date]:
    readings, _notes = read_document(html, accession="x", form="6-K", filed=date(2023, 8, 10), document_end=document_end,
                                     fiscal_year_end="1231", year_offset=0)
    return sorted(r.period_end for r in readings if r.statement == "balance")


def test_one_header_day_for_each_column_in_order():
    # Brookfield's 6-Ks: "AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)" in the label cell, over "2023 | 2022".
    header = ('<tr><td colspan="3">AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)</td><td colspan="3">2023</td>'
              '<td colspan="3"></td><td colspan="3">2022</td></tr>')
    assert ends(balance(header), date(2023, 6, 30)) == [date(2022, 12, 31), date(2023, 6, 30)]


def test_a_bare_year_at_the_left_edge_heads_its_column():
    # Brookfield's income statements: the year row starts with "2023" in the first cell, over the quarter's figures.
    header = ('<tr><td colspan="3">AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)</td></tr>'
              '<tr><td colspan="4">2023</td><td colspan="4"></td><td>2022</td></tr>')
    assert ends(balance(header), date(2023, 6, 30)) == [date(2022, 12, 31), date(2023, 6, 30)]


def test_each_year_takes_its_own_printed_date():
    # ASML's statutory interim report: "For the six-month period ended June 29, 2014 and June 30, 2013".
    header = ('<tr><td colspan="3">As at June 29, 2014 and June 30, 2013 (in millions)</td><td colspan="3">2014</td>'
              '<td colspan="3"></td><td colspan="3">2013</td></tr>')
    assert ends(balance(header), date(2014, 6, 29)) == [date(2013, 6, 30), date(2014, 6, 29)]


def test_dotted_dates_in_either_order():
    from statement_extractor.readers.printed.dates import _day_first
    assert _day_first("31.12.14 30.9.14 31.12.13")  # UBS
    assert not _day_first("12.31.2014 12.31.2013")  # Copel
    assert not _day_first("09.30.2014 06.30.2014")  # month first: each date then ends a month
    assert _day_first("30.06.2014 31.03.2014")


def test_a_column_holding_its_neighbours_day_keeps_its_own():
    # Ross's 1999 10-Q in fixed-width text: "May 1, January 30, May 2," over "1999 1999 1998"; the first column's cell
    # runs into the second's day, and had been dated January 30.
    from statement_extractor.readers.text.read import read_document as read_text
    text = """<TABLE>
ROSS STORES, INC.
CONDENSED CONSOLIDATED BALANCE SHEETS

($000)                                                        May 1, January 30,       May 2,
ASSETS                                                          1999        1999         1998
<S>                                                        <C>         <C>          <C>
  Cash and cash equivalents                                 $ 33,307    $ 80,083     $ 29,725
  Merchandise inventory                                      66,693      19,917       70,275
     Total Assets                                           100,000     100,000      100,000
  Accounts payable                                           40,000      35,000       45,000
     Total Stockholders' Equity                              60,000      65,000       55,000
     Total Liabilities and Stockholders' Equity             100,000     100,000      100,000
</TABLE>"""
    readings, _ = read_text(text, accession="x", form="10-Q", filed=date(1999, 6, 14), document_end=date(1999, 5, 1),
                            fiscal_year_end="0131", year_offset=0)
    assert sorted(r.period_end for r in readings) == [date(1998, 5, 2), date(1999, 1, 30), date(1999, 5, 1)]


def test_a_column_of_one_or_two_months_is_no_quarter_or_year_to_date():
    # Delta's 10-Q for June 2007, after fresh-start accounting on April 30: successor two months, predecessor one and
    # four months, beside 2006's three and six months. Only 2006's columns are a quarter and a year to date.
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.dates import column_periods
    head = ("<tr><td></td>" + "".join(f"<td>{w}</td>" for w in ["Two Months", "One Month", "Three", "Two Months", "Four Months", "Six"]) + "</tr>"
            + "<tr><td></td>" + "<td>Ended</td><td>Ended</td><td>Months Ended</td>" * 2 + "</tr>"
            + "<tr><td></td>" + "<td>June 30,</td><td>April 30,</td><td>June 30,</td>" * 2 + "</tr>"
            + "<tr><td>(in millions)</td>" + "<td>2007</td><td>2007</td><td>2006</td>" * 2 + "</tr>")
    body = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{n}</td>" for n in range(1, 7)) + "</tr>"
                   for label in ("Operating revenue", "Operating expense", "Income before income taxes", "Net income"))
    table = parse_html("<p>Consolidated Statements of Operations</p><table>" + head + body + "</table>")[0]
    periods = column_periods("income", [table], "1231", date(2007, 6, 30))
    assert sorted((p.end, p.duration) for p in periods.values() if p.duration) == [
        (date(2006, 6, 30), "quarter"), (date(2006, 6, 30), "ytd")]


def test_a_sentence_above_the_title_does_not_unmake_the_statement():
    # Visteon's 2012 10-K: its auditor's report ends "… the change in the presentation of the segment disclosures as
    # discussed in Note 22 …" just above "CONSOLIDATED STATEMENTS OF OPERATIONS"; the income statement had been skipped.
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    rows = "".join(f"<tr><td>{label}</td><td>{a}</td><td>{b}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in (
        ("Products", "6,857", "7,532"), ("Services", "260", "392"), ("Net sales", "7,117", "7,924"),
        ("Income before income taxes", "300", "250"), ("Provision for income taxes", "100", "90"), ("Net income", "200", "160")))
    page = ("<p>… and the change in the presentation of the segment disclosures as discussed in Note 22, as to which the date"
            " is February 28, 2013.</p><p>57 Table of Contents</p><p>VISTEON CORPORATION AND SUBSIDIARIES</p>"
            "<p>CONSOLIDATED STATEMENTS OF OPERATIONS</p><table><tr><td></td><td>Year Ended December 31</td>"
            "<td>Year Ended December 31</td><td>Three Months Ended December 31</td><td>Nine Months Ended October 1</td>"
            "</tr><tr><td>(Dollars in Millions, Except Per Share Amounts)</td><td>2012</td><td>2011</td><td>2010</td>"
            "<td>2010</td></tr>" + rows + "</table>")
    assert [kind for kind, _group in find_statements(parse_html(page))] == ["income"]


def test_a_sentence_describing_the_table_still_counts():
    # JPM's 10-Q for June 2001: "The following table sets forth selected financial data of the Investment Bank." above
    # a line-of-business table; it is no statement.
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    rows = "".join(f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in (
        ("Revenue", "7,899", "7,100"), ("Cash Expense", "4,933", "4,500"), ("Income before income taxes", "2,874", "2,600"),
        ("Income taxes", "900", "800"), ("Net income", "1,974", "1,800"), ("Average assets", "300,000", "290,000")))
    page = ("<p>INVESTMENT BANK The following table sets forth selected financial data of the Investment Bank.</p>"
            "<p>CONSOLIDATED STATEMENT OF INCOME DATA</p><table><tr><td></td><td>2Q 2001</td><td>2Q 2000</td></tr>" + rows + "</table>")
    assert find_statements(parse_html(page)) == []


def test_a_one_month_column_is_its_own_not_the_headers_span():
    # Morgan Stanley's 2010 10-K after moving its year end from November to December: "2010 | 2009 | Fiscal 2008 | One
    # Month Ended December 31, 2008". Its three years are read; the one month had emptied the whole statement.
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.dates import column_periods
    head = ("<tr><td></td><td>2010</td><td>2009</td><td>Fiscal 2008</td><td>One Month Ended December 31, 2008</td></tr>")
    body = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{n}</td>" for n in range(1, 5)) + "</tr>"
                   for label in ("Net revenues", "Non-interest expenses", "Income before income taxes", "Net income"))
    table = parse_html("<p>Consolidated Statements of Income (dollars in millions)</p><table>" + head + body + "</table>")[0]
    periods = column_periods("income", [table], "1231", date(2010, 12, 31))
    assert sorted((p.end.year, p.duration) for p in periods.values() if p.duration) == [(2008, "year"), (2009, "year"), (2010, "year")]


def test_a_column_printing_its_own_range_ends_on_the_ranges_last_day():
    # KEPCO's releases: "From Jan. 1, 2006 Through Mar. 31, 2006 | From Jan. 1, 2005 Through Mar. 31, 2005".
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.dates import column_periods
    head = ("<tr><td>(unit: in billions of Korean Won)</td><td>From Jan. 1, 2006 Through Mar. 31, 2006</td>"
            "<td>From Jan. 1, 2005 Through Mar. 31, 2005</td></tr>")
    body = "".join(f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in (
        ("Operating revenues", "6,852", "6,400"), ("Operating expenses", "6,000", "5,700"),
        ("Income before income taxes", "852", "700"), ("Net income", "600", "500")))
    table = parse_html("<p>STATEMENT OF INCOME for the first three months of 2006 and 2005</p><table>" + head + body + "</table>")[0]
    periods = column_periods("income", [table], "1231", date(2006, 3, 31))
    assert sorted(p.end for p in periods.values()) == [date(2005, 3, 31), date(2006, 3, 31)]


def test_a_range_start_set_day_first_among_month_first_ones():
    # Copel's June 2004 ITR: "04/01/2004 a 6/30/2004 | 01/01/2004 a 6/30/2004 | 01/04/2003 a 6/30/2003 | ...".
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.dates import column_periods
    head = ("<tr><td>Code</td><td>Description</td><td>04/01/2004 a 6/30/2004</td><td>01/01/2004 a 6/30/2004</td>"
            "<td>01/04/2003 a 6/30/2003</td><td>01/01/2003 a 6/30/2003</td></tr>")
    body = "".join(f"<tr><td>3.0{n}</td><td>{label}</td><td>1,263</td><td>2,538</td><td>996</td><td>2,008</td></tr>"
                   for n, label in enumerate(("Gross sales and/or services", "Net sales and/or services",
                                              "Operating profit (loss)", "Net income for the period"), 1))
    table = parse_html("<p>03.01- Statement of Income (in thousands of reais)</p><table>" + head + body + "</table>")[0]
    periods = column_periods("income", [table], "1231", date(2004, 6, 30))
    assert sorted((p.start, p.end) for p in periods.values()) == [
        (date(2003, 1, 1), date(2003, 6, 30)), (date(2003, 4, 1), date(2003, 6, 30)),
        (date(2004, 1, 1), date(2004, 6, 30)), (date(2004, 4, 1), date(2004, 6, 30))]


def test_months_from_and_to_over_years_give_the_span():
    # CEMEX's Q3 2026 report: "2026 | 2025" over the columns, "January - September" as the table's first row.
    from datetime import date as day
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.html.read import read_document
    html = "<p>STATEMENT OF OPERATIONS In thousands of U.S. dollars</p><table><tr><td>STATEMENT OF OPERATIONS</td><td>2026</td><td>2025</td></tr>" \
           "<tr><td>January - September</td></tr>" + "".join(
               f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in (
                   ("Sales", "13,202", "11,952"), ("Cost of sales", "(8,636)", "(7,900)"), ("Gross profit", "4,566", "4,052"),
                   ("Earnings before income tax", "1,300", "1,100"), ("Income tax", "(300)", "(250)"), ("Net income", "1,000", "850"))) + "</table>"
    assert parse_html(html)
    readings, _notes = read_document(html, accession="x", form="6-K", filed=day(2026, 10, 26), document_end=day(2026, 9, 30),
                                     fiscal_year_end="1231", year_offset=0)
    assert {(r.period_start, r.period_end) for r in readings} == {(day(2026, 1, 1), day(2026, 9, 30)), (day(2025, 1, 1), day(2025, 9, 30))}


def test_a_column_ending_after_its_filing_is_left_out():
    from datetime import date as day
    from statement_extractor.readers.html.read import read_document
    html = "<p>CONSOLIDATED STATEMENTS OF INCOME (in millions)</p><table><tr><td></td><td>2026</td><td>2025</td></tr>" + "".join(
        f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in (
            ("Sales", "100", "90"), ("Income before income taxes", "20", "18"), ("Income taxes", "5", "4"), ("Net income", "15", "14")))
    readings, notes = read_document(html + "</table>", accession="x", form="6-K", filed=day(2026, 10, 26), document_end=None,
                                    fiscal_year_end="1231", year_offset=0)
    assert {r.period_end.year for r in readings} == {2025} and any("ends after the filing" in note for note in notes)


def _income(header: str, first_row: str = "") -> list:
    from datetime import date as day
    from statement_extractor.readers.html.read import read_document
    html = f"<p>Income Statement (R$ million)</p><table><tr><td></td>{header}</tr>{first_row}" + "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>" for label, values in (
            ("Sales revenues", ("212", "236", "70", "71")), ("Income before income taxes", ("20", "18", "7", "6")),
            ("Income taxes", ("(5)", "(4)", "(2)", "(1)")), ("Net income", ("15", "14", "5", "5")))) + "</table>"
    readings, _notes = read_document(html, accession="x", form="6-K", filed=day(2020, 7, 31), document_end=day(2020, 6, 30),
                                     fiscal_year_end="1231", year_offset=0)
    return sorted((r.fiscal_year, r.fiscal_period, r.duration) for r in readings)


def test_each_columns_own_month_range_and_named_quarters():
    # Petrobras: "Jan-Jun/2020 | Jan-Jun/2019 | Apr-Jun/2020 | Apr-Jun/2019"; its 2016 release: "2016 | 2015 | 3Q-2016 | 2Q-2016" under "Jan-Sep".
    assert _income("<td>Jan-Jun/2020</td><td>Jan-Jun/2019</td><td>Apr-Jun/2020</td><td>Apr-Jun/2019</td>") == [
        ("2019", "Q2", "quarter"), ("2019", "Q2", "ytd"), ("2020", "Q2", "quarter"), ("2020", "Q2", "ytd")]
    assert _income("<td>2020</td><td>2019</td><td>2Q-2020</td><td>1Q-2020</td>", "<tr><td>Jan-Jun</td></tr>") == [
        ("2019", "Q2", "ytd"), ("2020", "Q1", "quarter"), ("2020", "Q2", "quarter"), ("2020", "Q2", "ytd")]
