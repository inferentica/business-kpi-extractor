"""Mexico's coded reporting template in a 6-K (Televisa's quarterly "kr6kfinan" documents)."""
from datetime import date

from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.mexico_coded import coded_readings


def _table(title: str, heads: list[list[str]], rows: list[tuple]) -> str:
    html = f"<p>{title}</p><table>"
    for head in heads:
        html += "<tr>" + "".join(f"<td>{cell}</td>" for cell in head) + "</tr>"
    for code, label, *values in rows:
        html += f"<tr><td>{code}</td><td>{label}</td>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
    return html + "</table>"


def _read(html: str):
    return coded_readings(parse_html(html), accession="x", form="6-K", filed=date(2013, 7, 12),
                          document_end=date(2013, 6, 30), fiscal_year_end="1231", year_offset=0)


def test_a_coded_balance_sheet_dated_by_its_title_and_proven_by_its_codes():
    html = _table("GRUPO TELEVISA, S.A.B. STATEMENT OF FINANCIAL POSITION AS OF JUNE 30, 2013 AND DECEMBER 31, 2012 "
                  "(THOUSANDS OF MEXICAN PESOS) CONSOLIDATED",
                  [["REF", "ACCOUNT / SUBACCOUNT", "CURRENT YEAR", "END OF PREVIOUS YEAR"], ["AMOUNT", "AMOUNT"]],
                  [("10000000", "TOTAL ASSETS", "170,030,776", "164,997,151"), ("11000000", "CURRENT ASSETS", "55,022,714", "54,637,754"),
                   ("12000000", "NON-CURRENT ASSETS", "115,008,062", "110,359,397"),
                   ("20000000", "TOTAL LIABILITIES", "98,383,313", "96,531,574"),
                   ("30000000", "STOCKHOLDERS' EQUITY", "71,647,463", "68,465,577")])
    readings = _read(html)
    assert {(r.period_end, r.status, r.currency, r.scope, r.lines[0]["value"]) for r in readings} == {
        (date(2013, 6, 30), "kept", "MXN", "consolidated", 170_030_776e3),
        (date(2012, 12, 31), "kept", "MXN", "consolidated", 164_997_151e3)}


def test_a_coded_income_statement_keys_revenue_by_its_code_not_its_translated_label():
    # "40010000 NET INCOME" is the revenue (ingresos netos); 40150000 is the net income.
    rows = [("40010000", "NET INCOME", "33,584,522", "18,065,013", "32,140,422", "16,983,775"),
            ("40020000", "COST OF SALES", "18,418,004", "9,429,888", "17,000,000", "9,000,000"),
            ("40030000", "GENERAL EXPENSES", "7,083,528", "3,608,174", "7,000,000", "3,500,000"),
            ("40040000", "INCOME (LOSS) BEFORE OTHER INCOME AND EXPENSE", "8,082,990", "5,026,951", "8,140,422", "4,483,775"),
            ("40110000", "INCOME (LOSS) BEFORE INCOME TAXES", "5,609,555", "3,320,326", "5,215,265", "2,800,000"),
            ("40120000", "INCOME TAXES", "1,864,567", "1,108,492", "1,475,798", "1,015,281"),
            ("40130000", "INCOME (LOSS) FROM CONTINUING OPERATIONS", "3,744,988", "2,211,834", "3,739,467", "1,784,719"),
            ("40140000", "INCOME (LOSS) FROM DISCONTINUED OPERATIONS, NET", "0", "0", "0", "0"),
            ("40150000", "NET INCOME (LOSS)", "3,744,988", "2,211,834", "3,739,467", "1,784,719")]
    html = _table("STATEMENTS OF COMPREHENSIVE INCOME FOR THE SIX AND THREE MONTHS ENDED JUNE 30, 2013 AND 2012 "
                  "(THOUSANDS OF MEXICAN PESOS) CONSOLIDATED",
                  [["REF", "ACCOUNT / SUBACCOUNT", "CURRENT YEAR", "PREVIOUS YEAR"],
                   ["CUMULATIVE", "QUARTER", "CUMULATIVE", "QUARTER"]], rows)
    readings = {(r.fiscal_year, r.duration): r for r in _read(html)}
    assert set(readings) == {("2013", "ytd"), ("2013", "quarter"), ("2012", "ytd"), ("2012", "quarter")}
    quarter = readings[("2013", "quarter")]
    assert quarter.status == "kept" and quarter.period_start == date(2013, 4, 1)
    concepts = {line["concept"]: line["value"] for line in quarter.lines}
    assert concepts["bmv_Revenue"] == 18_065_013e3 and concepts["bmv_ProfitLoss"] == 2_211_834e3


def test_other_comprehensive_income_and_data_tables_are_no_statement():
    html = _table("STATEMENTS OF COMPREHENSIVE INCOME OTHER COMPREHENSIVE INCOME (NET OF INCOME TAXES) FOR THE SIX AND "
                  "THREE MONTHS ENDED JUNE 30, 2013 AND 2012 (THOUSANDS OF MEXICAN PESOS) CONSOLIDATED",
                  [["REF", "ACCOUNT / SUBACCOUNT", "CURRENT YEAR", "PREVIOUS YEAR"], ["CUMULATIVE", "QUARTER", "CUMULATIVE", "QUARTER"]],
                  [("40200000", "NET INCOME (LOSS)", "3,744,988", "2,211,834", "3,739,467", "1,784,719")])
    assert _read(html) == []
