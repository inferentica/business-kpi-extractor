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


def _income(lines: str, scale: str = "(in millions)") -> str:
    return f"<p>Condensed Consolidated Statements of Earnings</p><p>{scale}</p><table>" \
           "<tr><td></td><td>2004</td><td>2003</td></tr>" + lines + "</table>"


def _currencies(html: str) -> set:
    from statement_extractor.readers.html.read import read_document
    readings, _notes = read_document(html, accession="x", form="6-K", filed=date(2030, 1, 1), document_end=None,
                                     fiscal_year_end="1231", year_offset=0)
    return {reading.currency for reading in readings}


ROWS = [("Sales", "958.1", "822.7"), ("Cost of goods sold", "248.6", "228.3"), ("Gross profit", "709.5", "594.4"),
        ("Earnings before income taxes", "300.0", "250.0"), ("Income taxes", "100.0", "50.0"),
        ("Net earnings", "200.0", "200.0")]


def test_a_dollar_sign_in_the_figures_cell_is_a_bare_dollar():
    # Alcon's 2004 6-Ks: "$ 880.2" in one cell, under "(in millions, except share data)".
    lines = "".join(f"<tr><td>{label}</td><td>{'$ ' if i == 0 else ''}{a}</td><td>{b}</td></tr>"
                    for i, (label, a, b) in enumerate(ROWS))
    assert _currencies(_income(lines)) == {BARE_DOLLARS}
    plain = "".join(f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in ROWS)
    assert _currencies(_income(plain)) == {None}


def test_a_text_tables_dollar_signs_are_a_bare_dollar():
    from statement_extractor.readers.text.parse import parse_text
    text = ("CONSOLIDATED STATEMENTS OF EARNINGS\n(in millions)\n<TABLE>\n" + " " * 40 + "2002        2001\n"
            + "\n".join(f"{label:<40}{'$' if i == 0 else ' '}{a:>8}    {b:>8}" for i, (label, a, b) in enumerate(ROWS))
            + "\n</TABLE>\n")
    assert any(table.dollars for table in parse_text(text))


def test_a_currency_in_the_tables_own_headings_is_its_currency():
    # UBS's restated time series: "USD million, except where indicated" over the dates, "Income statement" the first row.
    html = "<table><tr><td>USD million, except where indicated</td><td>31.12.18</td><td>31.12.17</td></tr>" \
           "<tr><td>Income statement</td></tr>" + "".join(
               f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in ROWS) + "</table>"
    from statement_extractor.readers.printed.currency import reporting_currency
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    [(_kind, group)] = find_statements(parse_html(html), release=True)
    assert reporting_currency(group, {}, "6-K") == "USD"


def test_a_statements_columns_share_its_one_dollar_sign():
    # Brookfield's Q2 2001 release: 2000 decided by the 2002 report alone (CAD); 2001 also by 2003's in US dollars.
    found = [reading("40-F", "FY", "CAD", "2002"), reading("40-F", "FY", "USD", "2003"),
             reading("6-K", "Q2", BARE_DOLLARS, "2001"), reading("6-K", "Q2", BARE_DOLLARS, "2000")]
    _bare_currencies(found)
    assert [r.currency for r in found[2:]] == ["CAD", "CAD"]
    alone = [reading("40-F", "FY", "CAD", "2002"), reading("40-F", "FY", "USD", "2003"), reading("6-K", "Q1", BARE_DOLLARS, "2001")]
    _bare_currencies(alone)
    assert alone[2].currency is None  # nothing in its own statement decides it


def test_brookfields_canadian_dollar_mark():
    assert currencies_named("MILLIONS, EXCEPT PER SHARE AMOUNTS (CDN.$)") == ["CAD"]


def test_a_currency_in_the_tables_first_row_beats_the_commentary_above_its_title():
    # Petrobras's real releases: "… debt … in U.S. dollars in the period. FINANCIAL STATEMENTS Income Statement", then
    # "R$ million" as the table's first row.
    html = "<p>mainly reflecting foreign exchange on debt in U.S. dollars in the period. FINANCIAL STATEMENTS Income Statement</p>" \
           "<table><tr><td></td><td>2016</td><td>2015</td></tr><tr><td>R$ million</td></tr>" + "".join(
               f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in ROWS) + "</table>"
    assert _currencies(html) == {"BRL"}


def test_the_sign_beside_each_column_tells_the_translation_from_the_statement():
    # FEMSA's 20-Fs: "Amounts expressed in millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.)", the
    # translation's column first, "$ 2,407" beside "Ps. 35,497".
    rows = [("Cash and cash equivalents", "$", "2,407", "Ps. 35,497", "Ps. 27,259"),
            ("Total current assets", "", "5,370", "79,112", "70,000"), ("Total assets", "", "15,000", "220,000", "200,000"),
            ("Total liabilities", "", "6,000", "88,000", "80,000"), ("Total equity", "", "9,000", "132,000", "120,000"),
            ("Total liabilities and equity", "", "15,000", "220,000", "200,000")]
    html = "<p>Consolidated Statements of Financial Position As of December 31, 2014 and 2013. Amounts expressed in millions " \
           "of U.S. dollars ($) and in millions of Mexican pesos (Ps.)</p><table><tr><td></td><td></td><td>December 2014 (*)</td>" \
           "<td>December 2014</td><td>December 2013</td></tr>" + "".join(
               "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"
    from statement_extractor.readers.html.read import read_document
    readings, notes = read_document(html, accession="x", form="20-F", filed=date(2015, 4, 15), document_end=date(2014, 12, 31),
                                    fiscal_year_end="1231", year_offset=0)
    got = {(r.period_end.year, r.currency, r.lines[0]["value"]) for r in readings}
    assert got == {(2014, "MXN", 35_497e6), (2013, "MXN", 27_259e6)}, got


def test_two_columns_under_one_heading_are_not_read():
    # FEMSA's FY2009 20-F: "2009" over the dollar translation, "2008" spanning its 2009 peso column and its 2008 one.
    from statement_extractor.domain.periods import Period
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import two_columns
    html = "<p>Amounts expressed in millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.)</p>" \
           "<table><tr><td></td><td colspan='2'>2009</td><td colspan='4'>2008</td></tr>" \
           "<tr><td>Net sales</td><td>$</td><td>15,018</td><td>Ps.</td><td>196,103</td><td>Ps.</td><td>167,171</td></tr>" \
           "<tr><td>Cost of sales</td><td></td><td>8,000</td><td></td><td>104,000</td><td></td><td>90,000</td></tr></table>"
    [table] = parse_html(html)
    periods = {(2,): Period(date(2009, 12, 31), date(2009, 1, 1)), (4, 6): Period(date(2008, 12, 31), date(2008, 1, 1))}
    assert two_columns(table, periods) == {(4, 6)}
    [plain] = parse_html(html.split("</p>", 1)[1])
    assert two_columns(plain, periods) == set()  # one currency: no translation beside it
