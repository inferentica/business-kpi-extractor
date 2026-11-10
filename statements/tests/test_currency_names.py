"""Currencies as foreign filers print them, and a bare "$" or "pesos" decided by the company's own reporting currency."""
from datetime import date

from statement_extractor.domain.basis import BARE_DOLLARS, BARE_PESOS, bare_currency, currencies_named
from statement_extractor.domain.model import Check, Reading
from statement_extractor.pipeline.company import _bare_currencies
from statement_extractor.pipeline.printed import _decided


def test_the_currencies_foreign_filers_print():
    assert currencies_named("(in millions of U.S.dollars, except earnings per share)") == ["USD"]  # Embraer
    assert currencies_named("(In thousands of Mexican pesos) (Notes 1, 2 and 3)") == ["MXN"]  # Televisa
    assert currencies_named("(In thousands of Chilean pesos)") == ["CLP"]  # Enel Chile
    assert currencies_named("ThCh$") == ["CLP"] and currencies_named("US$ (Note 2)") == ["USD"]


def test_a_bare_sign_names_only_the_kind():
    assert bare_currency("Consolidated Income Statement (unaudited) ($ millions except earnings per share)") == BARE_DOLLARS
    assert bare_currency("Consolidated Statement of Income (In millions of Dollars)") == BARE_DOLLARS  # Petrobras
    assert bare_currency("Consolidated statement of financial position (in thousands of pesos)") == BARE_PESOS  # GAP
    assert bare_currency("(MILLIONS, EXCEPT PER SHARE AMOUNTS)") is None  # Brookfield's releases name none


def test_a_bare_sign_takes_a_stated_currency_of_its_kind_only():
    assert _decided(BARE_DOLLARS, "USD") == "USD" and _decided(BARE_DOLLARS, "MXN") == BARE_DOLLARS
    assert _decided(None, "USD") == "USD" and _decided("CAD", "USD") == "CAD"


def reading(form, period, currency, year="2025"):
    return Reading(statement="income", duration="year" if period == "FY" else "quarter", fiscal_year=year,
                   fiscal_period=period, period_end=date(int(year), 12 if period == "FY" else 3, 31),
                   period_start=None, currency=currency, accession=f"{form}-{period}", form=form,
                   filed=date(int(year) + 1, 3, 1), own=True, source="html", lines=[], checks=[Check("calc", "pass")])


def test_a_release_printing_dollars_takes_the_annual_reports_dollar_currency():
    # Scotiabank: its 40-F's statements in Canadian dollars, its release's "($ millions)".
    found = [reading("40-F", "FY", "CAD"), reading("40-F", "FY", "CAD"), reading("6-K", "Q1", BARE_DOLLARS)]
    _bare_currencies(found)
    assert found[2].currency == "CAD"
    pesos = [reading("20-F", "FY", "USD"), reading("6-K", "Q1", BARE_PESOS)]  # an annual report in dollars: no peso
    _bare_currencies(pesos)
    assert pesos[1].currency is None


def test_figures_printed_with_a_dollar_sign_are_a_bare_dollar():
    # Alcon's 2010 releases: "(in millions, except share data)" over "Sales  $  1,812".
    from statement_extractor.readers.printed.currency import reporting_currency
    from statement_extractor.readers.printed.tables import Cell, Row, Table
    rows = [Row(label="Sales", level=0, figures={3: 1812.0}, cells=[Cell(0, 1, "Sales"), Cell(2, 1, "$"), Cell(3, 1, "1,812")])]
    group = [Table(rows=rows, header=[], context="Consolidated Statements of Earnings (in millions, except share data)", index=0)]
    assert reporting_currency(group, {(3,): None}, "6-K") == BARE_DOLLARS
