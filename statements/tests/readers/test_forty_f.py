"""Canadian filers' 40-F statements before XBRL: scales printed "($ millions)" or "(MILLIONS)", the currency stated in
the notes, "income before the undernoted", cash flow sections closed by unlabelled totals, and net income that follows
from a subtotal when no income before taxes is printed."""
from datetime import date

from statement_extractor.domain.basis import CANADIAN_GAAP, default_currency, stated_basis, stated_currency
from statement_extractor.readers.html.read import read_document


def cells(*items):
    return "".join(f"<td>{item}</td>" for item in items)


def table(title, header, rows):
    return (f"<p>{title}</p><table><tr>" + cells(*header) + "</tr>" +
            "".join("<tr>" + cells(*row) + "</tr>" for row in rows) + "</table>")


def read(html, end=date(2008, 10, 31), fye="1031"):
    readings, _ = read_document(html, accession="a", form="40-F", filed=date(2008, 12, 8), document_end=end,
                                fiscal_year_end=fye, year_offset=0)
    return {(r.statement, r.fiscal_year): r for r in readings}


# Scotiabank FY2008: "($ millions)", income before the undernoted, sections closed by unlabelled totals.
BANK = table("Consolidated Statement of Income", ["For the year ended October 31 ($ millions)", "2008", "2007"], [
    ("Net interest and other income", "11,246", "10,983"), ("Non-interest expenses", "7,296", "6,994"),
    ("Income before the undernoted", "3,950", "3,989"), ("Provision for income taxes", "691", "1,063"),
    ("Non-controlling interest in net income of subsidiaries", "119", "118"), ("Net income", "3,140", "2,808"),
]) + table("Consolidated Statement of Cash Flows", ["For the year ended October 31 ($ millions)", "2008", "2007"], [
    ("Cash flows from operating activities", "", ""), ("Net income", "3,140", "2,808"), ("Other, net", "16,972", "1,000"),
    ("", "20,112", "3,808"),
    ("Cash flows from financing activities", "", ""), ("Deposits", "31,767", "20,000"), ("", "31,767", "20,000"),
    ("Cash flows from investing activities", "", ""), ("Loans", "(51,740)", "(23,700)"), ("", "(51,740)", "(23,700)"),
    ("Effect of exchange rate changes on cash and cash equivalents", "297", "(100)"),
    ("Net change in cash and cash equivalents", "436", "8"),
    ("Cash and cash equivalents at beginning of year", "2,138", "2,130"),
    ("Cash and cash equivalents at end of year", "2,574", "2,138"),
])


def test_a_canadian_banks_statements():
    readings = read(BANK)
    assert readings[("income", "2008")].status == "kept" and readings[("income", "2008")].lines[0]["value"] == 11_246e6
    assert readings[("cash_flow", "2008")].status == "kept"
    # No currency is assumed from the form: Brookfield files 40-Fs in U.S. dollars.
    assert default_currency("40-F") is None and default_currency("20-F") is None and default_currency("10-K") == "USD"


# Brookfield FY2006: "(MILLIONS)", no income before taxes, net income from the subtotal above "Other items".
BROOKFIELD = table("CONSOLIDATED STATEMENTS OF INCOME", ["YEARS ENDED DECEMBER 31 (MILLIONS)", "2006", "2005"], [
    ("Total revenues", "6,897", "5,220"), ("Revenues less direct operating costs", "3,776", "2,319"),
    ("Expenses", "", ""), ("Interest", "1,185", "881"), ("Current income taxes", "142", "19"),
    ("Asset management and other operating costs", "333", "251"),
    ("Non-controlling interests in net income before the following", "468", "18"), ("", "1,648", "1,150"),
    ("Other items", "", ""), ("Equity accounted income (loss) from investments", "(36)", "20"),
    ("Depreciation and amortization", "(600)", "(374)"), ("Other provisions", "57", "(59)"),
    ("Future income taxes", "(203)", "19"), ("Non-controlling interests in the foregoing items", "304", "906"),
    ("Net income", "1,170", "1,662"),
])


def test_net_income_from_the_subtotal_above_it_and_a_bare_millions_scale():
    readings = read(BROOKFIELD, end=date(2006, 12, 31), fye="1231")
    income = readings[("income", "2006")]
    assert income.status == "kept" and income.lines[0]["value"] == 6_897e6
    notes = ("(a) Basis of Presentation All currency amounts are in United States dollars (U.S. dollars) unless otherwise "
             "stated. The consolidated financial statements present fairly, in all material respects, … in accordance "
             "with Canadian generally accepted accounting principles.")
    assert stated_currency(notes) == "USD" and stated_basis(notes) == CANADIAN_GAAP
    assert stated_currency("the U.S. dollar is the company's reporting currency") == "USD"
