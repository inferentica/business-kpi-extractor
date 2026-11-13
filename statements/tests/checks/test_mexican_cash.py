from datetime import date

from statement_extractor.checks.cash import cash_check
from statement_extractor.readers.html.read import read_document

_SECTIONS = [("Net cash flows provided by operating activities", 0, 18929.0), ("Net cash flows used in investing activities", 0, 6174.0),
             ("Net cash flows used in financing activities", 0, -11508.0)]


def test_femsas_net_cash_flow_to_its_ending_balance_before_discontinued_cash():
    # FEMSA's FY2010 20-F, 2009 column (round 81: "no opening and closing cash").
    figures = _SECTIONS + [("Net cash flow by continuing operations", 0, 3479.0), ("Net cash flow", 0, 7362.0),
                           ("Translation and restatement effect on cash and cash equivalents", 0, -1173.0),
                           ("Initial cash", 0, 9635.0), ("Initial cash of discontinued operations", 0, 0.0),
                           ("Initial cash and cash equivalents", 0, 9635.0), ("Ending balance", 0, 15824.0),
                           ("Ending balance by discontinued operations", 0, -1316.0),
                           ("Total ending balance of cash and cash equivalents, net", 0, 14508.0)]
    assert cash_check(figures).result == "pass"


def test_coca_cola_femsas_opening_net_of_restricted_cash():
    figures = _SECTIONS + [("Increase (decrease) in cash and cash equivalents", 0, 1911.0),
                           ("Translation and restatement effects", 0, -262.0), ("Initial cash and cash equivalents", 0, 6192.0),
                           ("Initial restricted cash", 0, -391.0), ("Initial balance, net", 0, 5801.0),
                           ("Increase (decrease) in restricted cash of the year", 0, 177.0),
                           ("Ending balance of cash and cash equivalents", 0, 7627.0)]
    assert cash_check(figures).result == "pass"
    figures[-1] = ("Ending balance of cash and cash equivalents", 0, 7628.0 + 5)
    assert cash_check(figures).result == "fail"


def test_coca_cola_femsas_initial_balance_of_cash():
    figures = _SECTIONS + [("(Decrease) increase in cash and cash equivalents", 0, -1039.0),
                           ("Translation and restatement effects", 0, 836.0),
                           ("Initial balance of cash and cash equivalents", 0, 12534.0),
                           ("Ending balance of cash and cash equivalents", 0, 12331.0)]
    assert cash_check(figures).result == "pass"


def _row(label, *cells):
    return "<tr><td>" + label + "</td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def test_a_continued_page_without_signs_is_split_as_the_first():
    # FEMSA's cash flows: the first page heads "2010" over "$ 1,908 | Ps. 23,632"; the next page heads "2010" again over
    # the two columns with no sign, and did not line up with the first (round 81).
    title = ("<p>FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. AND SUBSIDIARIES Consolidated Statements of Cash Flows For "
             "the years ended December 31, 2010 and 2009. Amounts expressed in millions of U.S. dollars ($) and in millions "
             "of Mexican pesos (Ps.).</p>")
    head = "<tr><td></td><td colspan='4'>2010</td><td colspan='2'>2009</td></tr>"
    first = (title + "<table>" + head + _row("Income before income taxes", "$", "1,908", "Ps.", "23,632", "Ps.", "16,758")
             + _row("Depreciation", "", "366", "", "4,527", "", "4,391") + _row("Income taxes paid", "", "(746", ")", "(9,230", ")", "(9,149", ")")
             + _row("Net cash flows provided by operating activities", "", "1,528", "", "18,929", "", "12,000")
             + _row("Net cash flows used in investing activities", "", "498", "", "6,174", "", "1,000") + "</table>")
    second = ("<p>F-4 Table of Contents</p><table>" + head
              + _row("Net cash flows used in financing activities", "", "(929", ")", "(11,508", ")", "(8,798", ")")
              + _row("Net cash flow", "", "1,097", "", "13,595", "", "4,202") + _row("Translation and restatement effect", "", "(81", ")", "(1,006", ")", "(1,173", ")")
              + _row("Initial cash and cash equivalents", "", "1,171", "", "14,508", "", "11,479")
              + _row("Total ending balance of cash and cash equivalents, net", "", "2,187", "", "27,097", "", "14,508") + "</table>")
    readings, notes = read_document(first + second, accession="x", form="20-F", filed=date(2011, 6, 29), document_end=date(2010, 12, 31),
                                    fiscal_year_end="1231", year_offset=0)
    own = next(r for r in readings if r.statement == "cash_flow" and r.period_end == date(2010, 12, 31))
    assert own.status == "kept" and own.currency == "MXN"
    assert not any("do not line up" in note for note in notes)
