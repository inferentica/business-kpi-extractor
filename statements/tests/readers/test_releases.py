"""Results releases furnished on 6-K: ASML's summary statements in one text block, TSMC's management reports with a
translation, percentages and changes beside each quarter, quarters named rather than dated, and the parent company's
statements beside the group's."""
from datetime import date

from statement_extractor.checks.across import cash_against_balances
from statement_extractor.readers.html.read import read_document as read_html
from statement_extractor.readers.text.read import read_document as read_text

# ASML's Q1 2003 release: the first lines above the <S> marker, labels set to the right, a block of ratios after the
# income statement and the cash flows run on in the balance sheet's block, with no opening or closing cash.
ASML_Q1_2003 = """<TABLE>
<CAPTION>
Summary Consolidated Statements of Operations
For the three months ended                                     March 31,         March 30,
                                                                    2002              2003
(Amounts in thousands except per share data)                         EUR               EUR
---------------------------------------------------------------------------------------------
                                  Net sales                      178,729           318,042
                              Cost of sales                      161,493           265,169
<S>                                                               <C>               <C>
                      Gross profit on sales                       17,236            52,873
                             Total expenses                      130,500           143,371
         Operating income (loss)                                (113,264)          (90,498)
        Loss before income taxes                                (125,579)          (97,532)
        Provision for income taxes                                39,683            28,843
                                   Net loss                      (85,896)          (68,689)

Ratios and Other Data
For the three months ended                                     March 31,         March 30,
                                                                    2002              2003
        Gross profit as a % of net sales                             9.6              16.6
        Sales of systems (units)                                      13                33
        Number of employees                                        6,852             6,261
</TABLE>
<TABLE>
<CAPTION>
Summary Consolidated Balance Sheets
                                                                 Dec 31,         March 30,
                                                                    2002              2003
(Amounts in thousands)                                               EUR               EUR
<S>                                                               <C>               <C>
                  Cash and cash equivalents                      668,760           688,297
                               Other assets                    2,632,928         2,553,730
                               Total assets                    3,301,688         3,242,027
                        Current liabilities                    1,986,172         2,009,013
                       Shareholders' equity                    1,315,516         1,233,014
 Total liabilities and Shareholders' equity                    3,301,688         3,242,027

Summary Consolidated Statements of Cash Flows
For the three months ended                                     March 31,         March 30,
                                                                    2002              2003
(Amounts in thousands)                                               EUR               EUR
  CASH FLOWS FROM OPERATING ACTIVITIES:
       Net cash provided by (used in) operating activities      (176,233)           58,838
       Net cash used in investing activities                     (22,441)          (18,868)
       Net cash provided by (used in) financing activities         4,639              (384)
       Effect of changes in exchange rates on cash                 2,527            (8,081)
       Net increase (decrease) in cash and cash equivalents     (191,508)           31,505
</TABLE>
"""


def test_a_text_release_with_statements_run_on_in_one_block():
    readings, _ = read_text(ASML_Q1_2003, accession="r", form="6-K", filed=date(2003, 4, 16),
                            document_end=date(2003, 3, 31), fiscal_year_end="1231", year_offset=0)
    by_key = {(r.statement, r.fiscal_year, r.fiscal_period): r for r in readings}
    income = by_key[("income", "2003", "Q1")]
    assert income.lines[0]["label"] == "Net sales" and income.lines[0]["value"] == 318_042e3
    assert not any("employees" in line["label"] for line in income.lines)  # the ratios are not the statement's
    assert by_key[("balance", "2003", "Q1")].status == "kept"
    flows = by_key[("cash_flow", "2003", "Q1")]
    assert flows.status == "unproven"
    # Proven by the balance sheets: 688,297 - 668,760 = 19,537, not the 31,505 printed here.
    assert cash_against_balances(readings) == 0 and flows.status == "unproven"


def test_cash_flows_proven_by_the_balance_sheets_cash():
    fixed = ASML_Q1_2003.replace("(191,508)           31,505", "(191,508)           19,537")
    readings, _ = read_text(fixed, accession="r", form="6-K", filed=date(2003, 4, 16), document_end=date(2003, 3, 31),
                            fiscal_year_end="1231", year_offset=0)
    assert cash_against_balances(readings) == 1
    flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
    assert flows.status == "kept"


def cells(*items):
    return "".join(f"<td>{item}</td>" for item in items)


# TSMC's Q4 2007 management report: each quarter in US$ (the latest only) and NT$ with its share of sales, then the
# changes; quarters named, not dated.
TSMC_Q4_2007 = ("<p>TAIWAN SEMICONDUCTOR MANUFACTURING COMPANY LIMITED AND SUBSIDIARIES Consolidated Condensed Statements of "
                "Income (Expressed in Millions of New Taiwan Dollars (NTD) and U.S. Dollars (USD))</p><table>"
                "<tr><td></td><td colspan='3'>Q4 2007</td><td colspan='2'>Q3 2007</td><td colspan='2'>Q4 2006</td>"
                "<td colspan='2'>QoQ</td></tr>"
                "<tr>" + cells("", "USD", "NTD", "%", "NTD", "%", "NTD", "%", "Amount", "%") + "</tr>"
                "<tr>" + cells("Net Sales", "2,894", "93,860", "100.0", "88,955", "100.0", "74,963", "100.0", "4,905", "5.5") + "</tr>"
                "<tr>" + cells("Cost of Sales", "(1,512)", "(49,024)", "(52.2)", "(48,231)", "(54.2)", "(40,510)", "(54.0)", "(793)", "1.6") + "</tr>"
                "<tr>" + cells("Gross Profit", "1,382", "44,836", "47.8", "40,724", "45.8", "34,453", "46.0", "4,112", "10.1") + "</tr>"
                "<tr>" + cells("Income before Income Tax", "1,382", "44,836", "47.8", "40,724", "45.8", "34,453", "46.0", "4,112", "10.1") + "</tr>"
                "<tr>" + cells("Income Tax", "(80)", "(2,600)", "(2.8)", "(2,500)", "(2.8)", "(2,000)", "(2.7)", "(100)", "4.0") + "</tr>"
                "<tr>" + cells("Net Income", "1,302", "42,236", "45.0", "38,224", "43.0", "32,453", "43.3", "4,012", "10.5") + "</tr>"
                "</table>")


def test_quarters_by_name_without_the_translation_percentages_or_changes():
    readings, notes = read_html(TSMC_Q4_2007, accession="m", form="6-K", filed=date(2008, 2, 1),
                                document_end=date(2007, 12, 31), fiscal_year_end="1231", year_offset=0)
    sales = {(r.fiscal_year, r.fiscal_period): r.lines[0]["value"] for r in readings}
    assert sales == {("2007", "Q4"): 93_860e6, ("2007", "Q3"): 88_955e6, ("2006", "Q4"): 74_963e6}
    assert all(r.currency == "TWD" and r.scope == "consolidated" for r in readings)


def test_the_parent_companys_statements_are_marked_so():
    parent = TSMC_Q4_2007.replace("COMPANY LIMITED AND SUBSIDIARIES Consolidated Condensed Statements of Income",
                                  "COMPANY LTD. UNCONSOLIDATED STATEMENTS OF INCOME")
    readings, _ = read_html(parent, accession="m", form="6-K", filed=date(2008, 2, 1), document_end=date(2007, 12, 31),
                            fiscal_year_end="1231", year_offset=0)
    assert readings and all(r.scope == "parent" for r in readings)


# ASML's Q2 2025 release: slide images with their words hidden beneath, the navigation naming every statement before
# the slide's own title.
def slide(words):
    return f'<DIV><IMG src="s.jpg"><DIV><FONT size="1" style="font-size:1pt;color:white">{words}</FONT></DIV></DIV>'


SLIDES = "<HTML><BODY>" + slide("Contents Consolidated Statements of Operations Consolidated Balance Sheets") + slide(
    "Three months ended Six months ended Jun 30, Jun 29, Jun 30, Jun 29, (unaudited, in millions &#8364;, except per "
    "share data) 2024 2025 2024 2025 Net system sales  4,760.9  5,596.1  8,726.8  11,336.5  Total cost of sales  "
    "(3,030.6)  (3,562.2)  (5,624.0)  (7,124.0)  Income before income taxes  1,822.7  2,688.9  3,240.3  5,476.0  Income "
    "tax expense  (291.6)  (487.4)  (515.6)  (952.5)  Net income  1,531.1  2,201.5  2,724.7  4,523.5    Summary US GAAP "
    "Notes ASML Financial statements US GAAP Q2 2025 2 Consolidated Statements of Operations Consolidated Balance Sheets "
    "Consolidated Statements of Cash Flows Summary US GAAP Consolidated Statements of Operations") + "</BODY></HTML>"


def test_a_release_printed_as_slides_with_hidden_words():
    from statement_extractor.readers.slides.parse import is_slides
    from statement_extractor.readers.slides.read import read_document as read_slides
    assert is_slides(SLIDES)
    readings, _ = read_slides(SLIDES, accession="s", form="6-K", filed=date(2025, 7, 16), document_end=date(2025, 6, 30),
                              fiscal_year_end="1231", year_offset=0)
    by_key = {(r.fiscal_year, r.fiscal_period, r.duration): r for r in readings}
    assert set(by_key) == {("2024", "Q2", "quarter"), ("2025", "Q2", "quarter"), ("2024", "Q2", "ytd"), ("2025", "Q2", "ytd")}
    latest = by_key[("2025", "Q2", "quarter")]
    assert latest.statement == "income" and latest.currency == "EUR" and latest.status == "kept"
    assert latest.lines[0]["value"] == 5_596.1e6 and by_key[("2025", "Q2", "ytd")].lines[0]["value"] == 11_336.5e6


ASML_Q4_2003 = """<TABLE>
<CAPTION>
ASML - Summary Consolidated Statements of Operations
                                     Three months ended December 31,   Twelve months ended December 31,
                                              2002             2003             2002              2003
(Amounts in thousands except per share data)   EUR              EUR              EUR               EUR
<S>                                          <C>              <C>            <C>               <C>
                         Net sales           819,626          525,510        1,958,672         1,542,737
      Loss before income taxes               (9,630)          23,268         (130,824)         (184,181)
      Benefit from income taxes                3,270          (7,560)          42,779            59,675
                Net income (loss)             (6,360)          15,708          (88,045)         (124,506)
</TABLE>
"""


def test_spans_centred_over_column_pairs_are_taken_in_order():
    readings, _ = read_text(ASML_Q4_2003, accession="r", form="6-K", filed=date(2004, 1, 15),
                            document_end=date(2003, 12, 31), fiscal_year_end="1231", year_offset=0)
    sales = {(r.fiscal_year, r.duration): r.lines[0]["value"] for r in readings}
    assert sales[("2003", "quarter")] == 525_510e3 and sales[("2003", "year")] == 1_542_737e3


def test_one_statement_of_profit_and_comprehensive_income_is_the_income_statement():
    html = ("<p>Taiwan Semiconductor Manufacturing Company Limited and Subsidiaries CONSOLIDATED STATEMENTS OF "
            "COMPREHENSIVE INCOME FOR THE THREE MONTHS ENDED MARCH 31, 2013 AND 2012 (In Thousands of New Taiwan "
            "Dollars)</p><table><tr><td></td><td>2013</td><td>2012</td></tr>" +
            "".join("<tr>" + cells(label, a, b) + "</tr>" for label, a, b in (
                ("NET REVENUE", "132,754,996", "105,614,831"), ("COST OF REVENUE", "71,988,726", "55,210,347"),
                ("GROSS PROFIT", "60,766,270", "50,404,484"), ("INCOME BEFORE INCOME TAX", "45,747,914", "35,673,054"),
                ("INCOME TAX EXPENSE", "6,170,000", "2,181,054"), ("NET INCOME", "39,577,914", "33,492,000"),
                ("Exchange differences arising on translation", "1,000", "(2,000)"))) + "</table>")
    readings, _ = read_html(html, accession="q", form="6-K", filed=date(2013, 5, 20), document_end=date(2013, 3, 31),
                            fiscal_year_end="1231", year_offset=0)
    assert {(r.statement, r.fiscal_year, r.fiscal_period, r.duration) for r in readings} == {
        ("income", "2013", "Q1", "quarter"), ("income", "2012", "Q1", "quarter")}


def test_a_title_in_the_tables_own_rows_says_consolidated():
    readings, _ = read_text(ASML_Q1_2003, accession="r", form="6-K", filed=date(2003, 4, 16),
                            document_end=date(2003, 3, 31), fiscal_year_end="1231", year_offset=0)
    assert readings and all(r.scope == "consolidated" for r in readings)


def test_a_release_without_ex99_reads_its_numbered_exhibits():
    # Brookfield's 6-K of 24 March 2011: its annual report filed as EX-1.
    from statement_extractor.sources.documents import release_documents

    class Item:
        def __init__(self, kind):
            self.document_type = kind

    cover, report, contract = Item("6-K"), Item("EX-1"), Item("EX-10.1")
    assert release_documents([cover, report, contract]) == [cover, report]
    release = Item("EX-99.1")
    assert release_documents([cover, report, release]) == [release]
    assert release_documents([cover, contract]) == [cover]
    letter = Item("EX-15.1")  # Petrobras: the interim report in the 6-K itself, an auditor's letter beside it
    assert release_documents([cover, letter]) == [cover, letter]


def test_a_hidden_sizing_row_is_no_line():
    # Brookfield's 2011 annual report: a row styled visibility:hidden holding "00000000" atop each statement.
    from statement_extractor.readers.html.parse import parse_html
    html = """<table><tr style="visibility:hidden; line-height:0pt"><td></td><td>00000000</td><td>00000000</td></tr>
<tr><td>YEARS ENDED DECEMBER 31 (MILLIONS)</td><td>2011</td><td>2010</td></tr>
<tr><td>Net income</td><td>3,674</td><td>3,195</td></tr></table>"""
    [table] = parse_html(html)
    assert [cell.text for cells in table.header for cell in cells][-2:] == ["2011", "2010"]
    assert [row.label for row in table.rows] == ["Net income"]


def test_another_issuers_release_in_the_6k_is_left_out():
    # FEMSA's 6-Ks carry Coca-Cola FEMSA's release, opening "(BMV: KOFL, NYSE: KOF)".
    from statement_extractor.pipeline.releases import _same_ticker
    from statement_extractor.readers.html.parse import parse_html
    html = "<p>Monterrey, Mexico, October 28, 2019 — FEMSA (NYSE: FMX; BMV: FEMSAUBD) announced. FEMSA and The " \
           "Coca-Cola Company (NYSE: KO) signed with Coca-Cola FEMSA (NYSE: KOF)</p><table><tr><td>a</td><td>1</td></tr></table>" \
           "<p>Mexico City, October 25, 2019, Coca-Cola FEMSA, S.A.B. de C.V. (BMV: KOFL, NYSE: KOF) announced</p>" \
           "<table><tr><td>b</td><td>2</td></tr></table>"
    assert [table.ticker for table in parse_html(html)] == ["", "KOF"]
    own = "<p>São Paulo, April 26, 2010 - Embraer (NYSE: ERJ) announced</p><table><tr><td>a</td><td>1</td></tr></table>"
    assert [table.ticker for table in parse_html(own)] == [""]  # the document's own issuer, under its ticker then
    assert _same_ticker("FMX", "FMX") and not _same_ticker("KOF", "FMX") and _same_ticker("AKO.A", "AKO-A")
