from datetime import date

from statement_extractor.readers.html.read import read_document
from statement_extractor.readers.printed.tables import number


def test_printed_figures():
    assert number("1,234") == 1234 and number("(1,234)") == -1234 and number("$ 5.20") == 5.2
    assert number("—") == 0 and number("-12") == -12 and number("Net sales") is None


def cells(*items):
    return "".join(f"<td>{item}</td>" for item in items)


# Apple's FY2007 10-K as printed: a "$" cell before some figures, a year header over each, the title above the table.
INCOME = """<p>CONSOLIDATED STATEMENTS OF OPERATIONS</p><p>(In millions, except share and per share amounts)</p>
<table>
<tr><td colspan="3">Three fiscal years ended September 29, 2007</td><td colspan="2">2007</td><td colspan="2">2006</td></tr>
<tr>""" + cells("Net sales", "", "", "$", "24,006", "$", "19,315") + """</tr>
<tr>""" + cells("Cost of sales", "", "", "", "15,852", "", "13,717") + """</tr>
<tr>""" + cells("Gross margin", "", "", "", "8,154", "", "5,598") + """</tr>
<tr>""" + cells("Operating expenses:", "", "", "", "", "", "") + """</tr>
<tr>""" + cells("", "Research and development", "", "", "782", "", "712") + """</tr>
<tr>""" + cells("", "Selling, general, and administrative", "", "", "2,963", "", "2,433") + """</tr>
<tr>""" + cells("", "", "Total operating expenses", "", "3,745", "", "3,145") + """</tr>
<tr>""" + cells("Operating income", "", "", "", "4,409", "", "2,453") + """</tr>
<tr>""" + cells("Other income and expense", "", "", "", "599", "", "365") + """</tr>
<tr>""" + cells("Income before provision for income taxes", "", "", "", "5,008", "", "2,818") + """</tr>
<tr>""" + cells("Provision for income taxes", "", "", "", "1,512", "", "829") + """</tr>
<tr>""" + cells("Net income", "", "", "$", "3,496", "$", "1,989") + """</tr>
<tr>""" + cells("Diluted", "", "", "$", "3.93", "$", "2.27") + """</tr>
</table>"""

# Coca-Cola's cash flows end with a heading and balances named under it.
CASH = """<p>CONSOLIDATED STATEMENTS OF CASH FLOWS</p>
<table>
<tr><td>Year Ended December 31,</td><td colspan="2">2006</td><td colspan="2">2005</td></tr>
<tr><td>(In millions)</td></tr>
<tr>""" + cells("Net cash provided by operating activities", "", "5,957", "", "6,423") + """</tr>
<tr>""" + cells("Net cash used in investing activities", "", "(1,700", ")", "(1,496") + """)</tr>
<tr>""" + cells("Net cash used in financing activities", "", "(6,583", ")", "(6,785") + """)</tr>
<tr>""" + cells("EFFECT OF EXCHANGE RATE CHANGES ON CASH AND CASH EQUIVALENTS", "", "65", "", "(148") + """)</tr>
<tr>""" + cells("CASH AND CASH EQUIVALENTS", "", "", "", "") + """</tr>
<tr>""" + cells("Net (decrease) increase during the year", "", "(2,261", ")", "(2,006") + """)</tr>
<tr>""" + cells("Balance at beginning of year", "", "4,701", "", "6,707") + """</tr>
<tr>""" + cells("Balance at end of year", "$", "2,440", "$", "4,701") + """</tr>
</table>"""


def read(html, fiscal_year_end, end):
    readings, notes = read_document(html, accession="a", form="10-K", filed=date(2007, 11, 15), document_end=end,
                                    fiscal_year_end=fiscal_year_end, year_offset=0)
    return {(r.statement, r.fiscal_year): r for r in readings}, notes


def test_an_income_statement_is_read_by_its_title_scale_and_column_headers():
    readings, _ = read(INCOME, "0929", date(2007, 9, 29))
    latest, earlier = readings[("income", "2007")], readings[("income", "2006")]
    assert latest.own and not earlier.own and latest.duration == "year"
    values = {line["label"]: line["value"] for line in latest.lines}
    assert values["Net sales"] == 24_006e6 and values["Diluted"] == 3.93
    assert values["Total operating expenses"] == 3_745e6
    assert latest.status == "kept" and earlier.status == "kept"
    levels = {line["label"]: line["level"] for line in latest.lines}
    assert levels["Research and development"] > levels["Net sales"]


def test_a_wrong_figure_fails_the_arithmetic():
    readings, _ = read(INCOME.replace("1,512", "1,500"), "0929", date(2007, 9, 29))
    assert readings[("income", "2007")].status == "rejected"
    readings, _ = read(INCOME.replace("3,745", "3,700"), "0929", date(2007, 9, 29))
    assert readings[("income", "2007")].status == "rejected"


def test_cash_balances_take_their_heading_and_brackets_in_their_own_cell_are_negatives():
    readings, _ = read(CASH, "1231", date(2006, 12, 31))
    flows = readings[("cash_flow", "2006")]
    assert flows.status == "kept"
    values = {line["label"]: line["value"] for line in flows.lines}
    assert values["Net cash used in investing activities"] == -1_700e6


def test_no_scale_no_statement():
    readings, notes = read(INCOME.replace("(In millions, except share and per share amounts)", ""), "0929", date(2007, 9, 29))
    assert not readings and any("scale" in note for note in notes)


def test_a_statement_never_runs_into_the_equity_statement_after_it():
    equity = """<p>CONSOLIDATED STATEMENTS OF SHAREOWNERS' EQUITY</p><table>
<tr><td>Year Ended December 31,</td><td>2006</td><td>2005</td></tr>
<tr><td>Balance at beginning of year</td><td>16,355</td><td>15,935</td></tr>
<tr><td>Net income</td><td>5,080</td><td>4,872</td></tr>
<tr><td>TOTAL SHAREOWNERS' EQUITY</td><td>16,920</td><td>16,355</td></tr></table>"""
    readings, _ = read(CASH + equity, "1231", date(2006, 12, 31))
    flows = readings[("cash_flow", "2006")]
    assert flows.status == "kept"
    assert not any("SHAREOWNERS" in line["label"] for line in flows.lines)


def test_note_references_beside_a_label_are_not_part_of_it():
    html = INCOME.replace("<td>Net sales</td>", "<td>Net sales</td><td>2, 26</td>").replace("""<td colspan="3">Three""", """<td colspan="4">Three""")
    readings, _ = read(html, "0929", date(2007, 9, 29))
    assert readings[("income", "2007")].lines[0]["label"] == "Net sales"


def table(title, header, rows):
    body = "".join("<tr>" + cells(*row) + "</tr>" for row in rows)
    return f"<p>{title}</p><table><tr>{header}</tr>{body}</table>"


def test_quarter_columns_dated_by_the_filing_and_cash_balances_named_by_their_dates():
    # JPM's 2002 10-Q: "Third Quarter | Nine Months" over bare years; cash and due from banks named by date.
    income = table("CONSOLIDATED STATEMENT OF INCOME (in millions)",
                   '<td colspan="2"></td><td colspan="2">Third Quarter</td>',
                   [["", "", "2002", "2001"], ["Total net revenue", "", "7,525", "7,000"],
                    ["Income before income tax expense", "", "2,430", "2,000"], ["Income tax expense", "", "830", "700"],
                    ["Net income", "", "1,600", "1,300"]])
    readings, _ = read(income, "1231", date(2002, 9, 30))
    quarter = readings[("income", "2002")]
    assert quarter.fiscal_period == "Q3" and quarter.duration == "quarter" and quarter.period_end == date(2002, 9, 30)
    flows = table("CONSOLIDATED STATEMENT OF CASH FLOWS (in millions)", "<td>Nine Months Ended September 30,</td><td>2002</td>",
                  [["Net cash provided by operating activities", "100"], ["Net cash used in investing activities", "(40)"],
                   ["Net cash used in financing activities", "(10)"], ["Net increase in cash and due from banks", "50"],
                   ["Cash and due from banks at December 31, 2001", "200"], ["Cash and due from banks at September 30, 2002", "250"]])
    readings, _ = read(flows, "1231", date(2002, 9, 30))
    assert readings[("cash_flow", "2002")].status == "kept"


def test_a_cash_flow_page_without_its_sections_proves_nothing():
    flows = table("CONSOLIDATED STATEMENTS OF CASH FLOWS (in millions)", "<td>Year Ended December 31,</td><td>2014</td>",
                  [["Net increase in cash and cash equivalents", "50"], ["Effect of exchange rate changes on cash", "1"],
                   ["Cash and cash equivalents, beginning of year", "200"], ["Cash and cash equivalents, end of year", "251"]])
    readings, _ = read(flows, "1231", date(2014, 12, 31))
    assert readings[("cash_flow", "2014")].status == "unproven"


def test_ifrs_titles_and_bare_totals():
    # TSMC: statements of financial position closing each side with a bare TOTAL.
    balance = table("CONSOLIDATED STATEMENTS OF FINANCIAL POSITION (In Millions of New Taiwan Dollars)",
                    "<td>Notes</td><td>December 31, 2014</td>",
                    [["Total current assets", "6", "900"], ["Total noncurrent assets", "", "100"], ["TOTAL", "", "1,000"],
                     ["Total liabilities", "", "400"], ["Total equity", "", "600"], ["TOTAL", "", "1,000"]])
    readings, _ = read(balance, "1231", date(2014, 12, 31))
    assert readings[("balance", "2014")].status == "kept"


def test_a_statement_named_inside_a_sentence_is_not_a_title():
    as_titled = INCOME.split("</p>", 1)[1]  # the scale line and the table, without the title
    html = "<p>Interest is shown as reported on the Consolidated Statement of Income.</p>" + as_titled
    readings, notes = read(html, "0929", date(2007, 9, 29))
    assert not readings and not notes
    readings, _ = read("<p>CONSOLIDATED STATEMENT OF INCOME</p>" + as_titled, "0929", date(2007, 9, 29))
    assert readings


def test_week_spans_numeric_dates_and_par_values_per_share():
    # PepsiCo's 2009 10-Q: "12 Weeks Ended" over "3/21/09 | 3/22/08"; Kroger's stock line names a par value per share.
    html = """<p>Condensed Consolidated Statement of Income</p><p>(in millions)</p><table>
<tr><td></td><td colspan="4">12 Weeks Ended</td></tr>
<tr><td></td><td colspan="2">3/21/09</td><td colspan="2">3/22/08</td></tr>
<tr>""" + cells("Net Revenue", "$", "8,263", "$", "8,333") + """</tr>
<tr>""" + cells("Cost of sales", "", "3,744", "", "3,834") + """</tr>
<tr>""" + cells("Income before income taxes", "", "1,500", "", "1,600") + """</tr>
<tr>""" + cells("Provision for income taxes", "", "400", "", "420") + """</tr>
<tr>""" + cells("Net Income", "$", "1,100", "$", "1,180") + """</tr></table>
<p>Condensed Consolidated Balance Sheet</p><p>(in millions)</p><table>
<tr><td></td><td colspan="2">3/21/09</td><td colspan="2">12/27/08</td></tr>
<tr>""" + cells("Cash", "$", "2,000", "$", "1,900") + """</tr>
<tr>""" + cells("Property, plant and equipment", "", "1,500", "", "1,400") + """</tr>
<tr>""" + cells("Accumulated depreciation", "", "(500", ")", "(400") + """)</tr>
<tr><td></td><td></td><td>1,000</td><td></td><td>1,000</td></tr>
<tr>""" + cells("Total Assets", "$", "3,000", "$", "2,900") + """</tr>
<tr><td>Liabilities</td></tr>
<tr>""" + cells("Accounts payable", "", "1,045", "", "945") + """</tr>
<tr>""" + cells("Long-term debt", "", "1,000", "", "1,000") + """</tr>
<tr>""" + cells("Total Liabilities", "", "2,045", "", "1,945") + """</tr>
<tr><td>Equity</td></tr>
<tr>""" + cells("Common stock, $1 par per share, 955 shares issued", "", "955", "", "955") + """</tr>
<tr>""" + cells("Total Equity", "", "955", "", "955") + """</tr>
<tr>""" + cells("Total Liabilities and Equity", "$", "3,000", "$", "2,900") + """</tr></table>"""
    readings, _ = read_document(html, accession="a", form="10-Q", filed=date(2009, 4, 22), document_end=date(2009, 3, 21),
                                fiscal_year_end="1226", year_offset=0)
    income = next(r for r in readings if r.statement == "income" and r.own)
    assert income.duration == "quarter" and income.period_end == date(2009, 3, 21) and income.status == "kept"
    balance = next(r for r in readings if r.statement == "balance" and r.own)
    values = {line["label"]: line["value"] for line in balance.lines}
    assert values["Common stock, $1 par per share, 955 shares issued"] == 955e6
    assert balance.status == "kept"


def test_titles_in_layout_tables_in_the_tables_own_rows_and_scales_in_italics():
    # Morgan Stanley titles a statement in a one-row layout table; PepsiCo prints the title as the table's first row in
    # divs; Stock Yards puts its scale in italics; "Beginning cash" and "Ending cash" open and close.
    rows = [("Net cash provided by operating activities", "900", "800"), ("Net cash used in investing activities", "(500)", "(400)"),
            ("Net cash used in financing activities", "(300)", "(300)"), ("Net change in cash and cash equivalents", "100", "100"),
            ("Beginning cash and cash equivalents", "1,000", "900"), ("Ending cash and cash equivalents", "1,100", "1,000")]
    body = "".join("<tr>" + cells(label, "", now, "", before) + "</tr>" for label, now, before in rows) + "</table>"
    header = """<tr><td></td><td colspan="4">Six Months Ended June 30,</td></tr>
<tr><td></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>"""
    layout = """<table><tr><td><b>Consolidated Cash Flow Statements</b></td><td><img src="logo.jpg"></td></tr></table>
<p><i>(in thousands)</i></p><table>""" + header + body
    own_row = """<p><i>(in thousands)</i></p><table><tr><td colspan="5"><div>Condensed Consolidated Statement of Cash
Flows</div><div>PepsiCo, Inc. and Subsidiaries</div></td></tr>""" + header + body
    for html in (layout, own_row):
        readings, notes = read_document(html, accession="a", form="10-Q", filed=date(2026, 8, 4),
                                        document_end=date(2026, 6, 30), fiscal_year_end="1231", year_offset=0)
        flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
        assert flows.status == "kept" and flows.duration == "ytd" and flows.lines[0]["value"] == 900e3, notes


def test_a_comprehensive_income_statement_is_not_the_income_statement():
    from statement_extractor.readers.printed.find import TITLES
    income = dict(TITLES)["income"]
    assert income.search("Consolidated Income Statements") and not income.search("Consolidated Comprehensive Income Statements")


def test_a_data_caption_in_a_tables_first_rows_is_not_a_title():
    # Prudential's selected financial data: its first row reads "Income Statement Data".
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    html = """<p>Item 6. Five-year data</p><table><tr><td>Income Statement Data:</td></tr>
<tr><td></td><td>2003</td><td>2002</td></tr>""" + "".join(
        "<tr>" + cells(label, "1,000", "900") + "</tr>" for label in ("Total revenues", "Income before income taxes",
                                                                      "Income taxes", "Net income", "Total assets"))
    assert find_statements(parse_html(html + "</table>")) == []


def test_average_balance_sheets_in_the_discussion_are_no_statement():
    # SYBT's 10-Q MD&A: "The following table presents average balance sheets for the three month periods ended …".
    html = ("<p>The following table presents average balance sheets for the three month periods ended March 31, 2017 "
            "and 2016 along with the related calculation of tax-equivalent net interest income.</p><table>"
            "<tr><td>Three months ended March 31,</td><td>2017</td><td>2016</td></tr>" +
            "".join("<tr>" + cells(label, a, b) + "</tr>" for label, a, b in (
                ("Loans", "2,036,437", "1,900,000"), ("Total earning assets", "2,673,842", "2,500,000"),
                ("Total assets", "2,818,072", "2,640,000"), ("Total liabilities", "2,525,532", "2,370,000"),
                ("Total stockholders' equity", "292,540", "270,000"),
                ("Total liabilities and stockholders' equity", "2,818,072", "2,640,000"))) + "</table>")
    readings, _ = read(html, "1231", date(2017, 3, 31))
    assert not readings


def test_share_counts_under_a_heading_that_names_their_scale():
    # Apple's FY2003 10-K: "Shares used in computing earnings (loss) per share (in thousands):" is the heading of the
    # share counts below it, not a caption to skip, so "Basic" is 345,613 thousand shares, not 345,613 a share.
    html = INCOME.replace("""<tr>""" + cells("Diluted", "", "", "$", "3.93", "$", "2.27") + """</tr>""",
                          """<tr>""" + cells("Diluted", "", "", "$", "3.93", "$", "2.27") + """</tr>
<tr>""" + cells("Shares used in computing earnings per share (in thousands):", "", "", "", "", "", "") + """</tr>
<tr>""" + cells("Basic", "", "", "", "861,199", "", "844,058") + """</tr>""")
    readings, _ = read(html, "0929", date(2007, 9, 29))
    values = [(line["label"], line["value"]) for line in readings[("income", "2007")].lines]
    assert ("Diluted", 3.93) in values and ("Basic", 861_199e3) in values


def test_a_caption_row_with_words_before_it_is_still_a_caption():
    # ASML's FY2000 report: "For the year ended December 31 (Amounts in thousands, except per share data)" above its
    # lines; its "per share" names no line, so net income stays in millions.
    html = INCOME.replace("""<tr>""" + cells("Net sales", "", "", "$", "24,006", "$", "19,315") + """</tr>""",
                          """<tr>""" + cells("For the year ended (In millions, except per share data)", "", "", "", "", "", "")
                          + """</tr><tr>""" + cells("Net sales", "", "", "$", "24,006", "$", "19,315") + """</tr>""")
    readings, _ = read(html, "0929", date(2007, 9, 29))
    values = {line["label"]: line["value"] for line in readings[("income", "2007")].lines}
    assert values["Net income"] == 3_496e6 and values["Net sales"] == 24_006e6


def test_a_share_heading_wrapped_over_two_lines():
    # Apple's FY1998 Q2 10-Q: "Shares used in computing earnings (loss)" / "per common share (in thousands):".
    html = INCOME.replace("""<tr>""" + cells("Diluted", "", "", "$", "3.93", "$", "2.27") + """</tr>""",
                          """<tr>""" + cells("Diluted", "", "", "$", "3.93", "$", "2.27") + """</tr>
<tr>""" + cells("Shares used in computing earnings (loss)", "", "", "", "", "", "") + """</tr>
<tr>""" + cells("per common share (in thousands):", "", "", "", "", "", "") + """</tr>
<tr>""" + cells("Basic", "", "", "", "125,609", "", "122,500") + """</tr>""")
    readings, _ = read(html, "0929", date(2007, 9, 29))
    values = [(line["label"], line["value"]) for line in readings[("income", "2007")].lines]
    assert ("Basic", 125_609e3) in values


def test_a_raised_footnote_mark_is_not_part_of_the_label():
    # Lloyds' 2025 releases set the mark as a <font> styled vertical-align: super, not a <sup>.
    from statement_extractor.readers.html.parse import parse_html
    mark = '<font style="font-size: 5.2pt; vertical-align: super;">1<font style="display: inline-block;"></font></font>'
    page = (f"<table><tr><td></td><td>2025</td></tr><tr><td><font>Other income</font>{mark}</td><td>1,342</td></tr>"
            "</table>")
    table = parse_html(page)[0]
    assert [(row.label, row.figures) for row in table.rows if row.figures] == [("Other income", {1: 1342.0})]


def test_a_mark_moved_up_and_a_trailing_currency_sign_leave_the_label():
    from statement_extractor.readers.html.parse import parse_html
    mark = '<span style="font-size:5.85pt;position:relative;top:-3.15pt;vertical-align:baseline">1</span>'
    page = ('<meta charset="utf-8"><table><tr><td></td><td>2026</td></tr>'
            f"<tr><td><span>Basic Net Income Per Share</span>{mark}</td><td>0.86</td></tr>"
            "<tr><td>Profit for the period ￦</td><td>1,234</td></tr></table>")
    assert [row.label for row in parse_html(page)[0].rows if row.figures] == ["Basic Net Income Per Share",
                                                                              "Profit for the period"]
