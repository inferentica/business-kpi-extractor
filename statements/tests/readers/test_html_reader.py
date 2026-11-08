from datetime import date

from statement_extractor.readers.html.read import read_document
from statement_extractor.readers.html.tables import number


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
