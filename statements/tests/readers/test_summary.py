"""Multi-year summary tables: each year's key figures, read in printed order where a PDF's columns drift, the second
basis's lines (a reconciliation) left out, and checked against the statements before any is served."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.readers.printed.summary import read_summaries
from statement_extractor.readers.text.parse import parse_text
from statement_extractor.selection.summaries import choose_summaries

# TSMC's FY2001 20-F, filed on paper, as its PDF lays out: figures drift right of the years; a US GAAP block follows.
TSMC = """
             Item 3. Key Information
             Selected Financial Data
                     The selected income statement data for the years ended December 31, 1999,  2000 and
             2001 set forth below are derived from our audited consolidated financial statements.
                                                                     Year ended and as of December 31,
                                                     1997        1998       1999        2000         2001       2001
                                                     NT$         NT$        NT$         NT$          NT$        US$
                                                                       (in millions, except percentages,
                                                              earnings per share and per ADS, and operating data)
               Income Statement Data:
               ROC GAAP
               Net sales...........................................  43,927  50,524  76,305  166,198  125,885  3,597
               Cost of sales(1).................................  (23,801)  (33,009)  (45,212)  (87,610)  (92,228)  (2,635)
               Income from operations....................  14,622  12,305  22,270     61,295      12,778         365
               Net income........................................  17,974  14,389  23,527  65,106  14,483        414
               Earnings per share(2)........................  1.25  0.98    1.57        3.97        0.83        0.02
               US GAAP
               Net sales...........................................  43,927  50,524  76,305  166,860  127,242  3,636
               Net income (loss)..............................  10,039  1,249  13,884  21,740    (21,975)       (628)
               Balance Sheet Data:
               ROC GAAP
               Total assets.......................................  137,318  165,461  235,436  370,886  366,518  10,472
               Shareholders’ equity.........................  81,784  96,285  152,571  261,754   277,190       7,920
"""


def read(text=TSMC):
    return read_summaries(parse_text(text), accession="fy2001", form="20-F", filed=date(2002, 6, 28),
                          fiscal_year_end="1231", year_offset=0)


def test_each_year_in_printed_order_and_the_reconciliation_left_out():
    figures = {(row["fiscal_year"], row["name"]): row["value"] for row in read()}
    assert figures[("1997", "revenue")] == 43_927e6 and figures[("2001", "revenue")] == 125_885e6
    assert figures[("1999", "net income")] == 23_527e6  # ROC GAAP, not the US GAAP 13,884
    assert figures[("1997", "total assets")] == 137_318e6 and figures[("2001", "equity")] == 277_190e6
    assert not any(value in (3_597e6, 10_472e6) for value in figures.values())  # the US$ translation


def test_a_line_missing_a_year_is_left_out_rather_than_guessed():
    text = TSMC.replace("Total assets.......................................  137,318  165,461",
                        "Total assets.......................................  165,461")
    assert not any(row["name"] == "total assets" for row in read(text))


def income(year, net, filed, accession=None):
    return Reading(statement="income", duration="year", fiscal_year=year, fiscal_period="FY",
                   period_end=date(int(year), 12, 31), period_start=None, currency="TWD", accession=accession or f"a{year}",
                   form="20-F", filed=filed, own=True, source="archive", basis="ROC GAAP",
                   lines=[{"line": 0, "concept": "printed_net_income", "label": "Net income", "level": 0,
                           "is_heading": False, "value": net}], checks=[Check("calc", "pass")])


def chosen_of(readings):
    return [{"statement": r.statement, "fiscal_year": r.fiscal_year, "fiscal_period": "FY", "duration": "year",
             "status": "kept"} for r in readings]


def test_a_table_that_agrees_with_the_statements_serves_only_the_years_before_them():
    # 1999 as first printed differs: a restatement the FY2001 summary carries, not a reason to doubt it.
    statements = [income("1999", 24_000e6, date(2000, 6, 1)), income("2000", 65_106e6, date(2002, 6, 28), "fy2001"),
                  income("2001", 14_483e6, date(2002, 6, 28), "fy2001")]
    result = choose_summaries(read(), statements, chosen_of(statements))
    assert result["tables"]["fy2001#0"]["verdict"] == "verified" and result["tables"]["fy2001#0"]["restated"] == 1
    years = {(row["fiscal_year"], row["name"]) for row in result["years"]}
    assert ("1997", "revenue") in years and ("1999", "total assets") in years
    assert ("2000", "net income") not in years and ("2001", "revenue") not in years  # statements cover these


def test_a_table_dated_wrong_is_left_out_whole():
    # The same figures a year off: the filing's own 2000 statement prints 14,483 where the table says 65,106.
    statements = [income("2000", 14_483e6, date(2002, 6, 28), "fy2001")]
    result = choose_summaries(read(), statements, chosen_of(statements))
    assert result["tables"]["fy2001#0"]["verdict"] == "contradicted" and result["years"] == []
