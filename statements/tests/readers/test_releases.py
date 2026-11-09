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
