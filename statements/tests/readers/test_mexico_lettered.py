from datetime import date

from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.mexico_lettered import is_lettered, lettered_readings


def _table(context: str, rows: list[tuple]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<p>{context}</p><table>{body}</table>"


def _document(closing: str = "33,109,960") -> str:
    # Televisa's 6-K of July 2010 (kr6k-2qfinan_grupo.htm), cut down: lettered codes in a column of their own, each
    # amount beside its share of the total, a cash flow statement without dates.
    cover = "<p>MEXICAN STOCK EXCHANGE STOCK EXCHANGE CODE: TLEVISA QUARTER:&#160;&#160; 02 YEAR:&#160; 2010</p>"
    balance = _table("GRUPO TELEVISA, S.A.B. BALANCE SHEETS AS OF JUNE 30, 2010 AND 2009 (Thousands of Mexican Pesos) "
                     "CONSOLIDATED Final Printing", [
        ("REF", "CONCEPTS", "CURRENT YEAR", "", "PREVIOUS YEAR", ""), ("S", "", "Amount", "%", "Amount", "%"),
        ("s01", "TOTAL ASSETS", "122,961,469", "100", "111,820,954", "100"),
        ("s03", "CASH AND AVAILABLE INVESTMENTS", "33,109,960", "27", "29,820,824", "27"),
        ("s20", "TOTAL LIABILITIES", "76,108,185", "62", "70,000,000", "63"),
        ("s21", "CURRENT LIABILITIES", "15,245,071", "12", "14,000,000", "13"),
        ("s27", "LONG-TERM LIABILITIES", "41,959,338", "34", "38,000,000", "34"),
        ("s31", "DEFERRED LIABILITIES", "14,146,723", "12", "13,000,000", "12"),
        ("s32", "OTHER NON-CURRENT LIABILITIES", "4,757,053", "4", "5,000,000", "4"),
        ("s33", "STOCKHOLDERS' EQUITY", "46,853,284", "38", "41,820,954", "37"),
        ("s34", "NONCONTROLLING INTEREST", "6,411,175", "5", "6,000,000", "5"),
        ("s35", "CONTROLLING INTEREST", "40,442,109", "33", "35,820,954", "32")])
    cash = _table("STATEMENTS OF CASH FLOWS (INDIRECT METHOD) MAIN CONCEPTS (Thousands of Mexican Pesos) Final Printing "
                  "CONSOLIDATED", [
        ("REF", "CONCEPTS", "CURRENT YEAR", "PREVIOUS YEAR"), ("E", "", "Amount", "Amount"),
        ("e07", "NET CASH FLOWS PROVIDED BY OPERATING ACTIVITIES", "8,207,480", "4,366,389"),
        ("e08", "NET CASH FLOWS FROM INVESTING ACTIVITIES", "(2,215,103)", "(1,504,408)"),
        ("e09", "CASH IN EXCESS (REQUIRED) FOR FINANCING ACTIVITIES", "5,992,377", "2,861,981"),
        ("e10", "NET CASH FLOWS FROM FINANCING ACTIVITIES", "(2,840,339)", "(8,037,002)"),
        ("e11", "NET (DECREASE) INCREASE IN CASH AND CASH EQUIVALENTS", "3,152,038", "(5,175,021)"),
        ("e12", "TRANSLATION DIFFERENCES IN CASH AND CASH EQUIVALENTS", "(46,528)", "(110,215)"),
        ("e13", "CASH AND CASH EQUIVALENTS AT BEGINNING OF PERIOD", "30,004,450", "35,106,060"),
        ("e14", "CASH AND CASH EQUIVALENTS AT END OF PERIOD", closing, "29,820,824")])
    return cover + balance + cash


def _read(content: str):
    return {(r.statement, r.period_end): r for r in lettered_readings(
        parse_html(content), content, accession="0000895345-10-000225", form="6-K", filed=date(2010, 7, 15),
        document_end=date(2010, 6, 30), fiscal_year_end="1231", year_offset=0)}


def test_the_earlier_template_is_read_by_its_codes():
    content = _document()
    assert is_lettered(content)
    readings = _read(content)
    assert set(readings) == {("balance", date(2010, 6, 30)), ("balance", date(2009, 6, 30)),
                             ("cash_flow", date(2010, 6, 30)), ("cash_flow", date(2009, 6, 30))}
    balance = readings["balance", date(2009, 6, 30)]  # the same quarter a year before, not the year end
    assert (balance.fiscal_period, balance.own, balance.status, balance.currency) == ("Q2", False, "kept", "MXN")
    assert balance.lines[0] == {"line": 0, "concept": "bmv_Assets", "code": "s01", "label": "TOTAL ASSETS", "level": 0,
                                "is_heading": False, "value": 111_820_954_000}  # not its share "100"
    flows = readings["cash_flow", date(2010, 6, 30)]
    assert (flows.period_start, flows.duration, flows.status) == (date(2010, 1, 1), "ytd", "kept")


def test_a_cash_flow_whose_closing_cash_is_not_the_balance_sheets_is_not_dated():
    readings = _read(_document(closing="33,109,970").replace("30,004,450", "30,004,460"))
    assert ("cash_flow", date(2010, 6, 30)) not in readings
    assert ("cash_flow", date(2009, 6, 30)) in readings
