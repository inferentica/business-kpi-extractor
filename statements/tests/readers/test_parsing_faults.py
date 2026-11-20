"""Printed and plain-text parsing faults (s7-parsing; review C1, C2, C4)."""
from statement_extractor.readers.printed.tables import number
from statement_extractor.readers.text.lines import split


def figures(line: str) -> tuple[str, list[float]]:
    parsed = split(line)
    return parsed.label, [number(chunk.text) for chunk in parsed.figures]


def test_a_bracket_padded_away_from_its_figure_is_still_negative():
    # 1990s plain-text tables pad a bracket away from its figure: "(  2,000)" and "( 1,234)" are -2,000 and -1,234.
    assert figures("Capital expenditures   (  2,000)   (  1,500)") == ("Capital expenditures", [-2000.0, -1500.0])
    assert figures("Net loss   ( 1,234)   2,000") == ("Net loss", [-1234.0, 2000.0])
    assert figures("Repayment of debt   $(  1,000)   $(    734)") == ("Repayment of debt", [-1000.0, -734.0])
    assert figures("Other (Note 3)   1,234   (12)") == ("Other (Note 3)", [1234.0, -12.0])


def test_a_note_number_is_never_the_amount_of_a_blank_dated_cell():
    # "Assets held for sale | 12 | (blank) | 150": the note's number is no figure for 2005 (review C2; CSN's 2017 20-F
    # dividends line held its note number 20).
    from datetime import date
    from statement_extractor.readers.html.read import read_document

    def row(*items):
        return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"

    html = ("<p>CONSOLIDATED BALANCE SHEETS</p><p>(In thousands)</p><table>"
            + row("December 31,", "Note", "2005", "2004")
            + row("Cash", "4", "1,000", "900")
            + row("Assets held for sale", "12", "", "150")
            + row("Total assets", "", "1,000", "1,050")
            + row("Liabilities", "", "400", "450")
            + row("Equity", "", "600", "600")
            + row("Total liabilities and equity", "", "1,000", "1,050") + "</table>")
    readings, _ = read_document(html, accession="a", form="10-K", filed=date(2006, 3, 1), document_end=date(2005, 12, 31),
                                fiscal_year_end="1231", year_offset=0)
    year = next(r for r in readings if r.statement == "balance" and r.fiscal_year == "2005")
    values = {line["label"]: line["value"] for line in year.lines}
    assert values.get("Assets held for sale") in (None, 0.0) and values["Cash"] == 1_000_000.0


def caption(text):
    from statement_extractor.readers.printed.tables import Table
    return [Table(rows=[], header=[], context=text, index=0)]


def test_a_per_share_exception_leaves_share_counts_in_the_money_scale():
    from statement_extractor.readers.printed.scales import share_scale
    for text in ("(In millions, except per-share amounts)", "(In millions, except per common share data)",
                 "(In millions, except per share data)"):
        assert share_scale(caption(text), 1e6) == 1e6, text
    assert share_scale(caption("(In millions, except share and per share amounts)"), 1e6) == 1.0
    assert share_scale(caption("(In millions, except number of shares)"), 1e6) == 1.0


def test_millions_written_as_zeros():
    from statement_extractor.readers.printed.scales import statement_scale
    assert statement_scale(caption("CONSOLIDATED STATEMENTS OF INCOME (000,000 omitted)")) == 1e6
    assert statement_scale(caption("CONSOLIDATED STATEMENTS OF INCOME ($000,000 omitted, except per share)")) == 1e6
    assert statement_scale(caption("CONSOLIDATED STATEMENTS OF INCOME (000 omitted)")) == 1e3
    assert statement_scale(caption("CONSOLIDATED STATEMENTS OF INCOME ($000's)")) == 1e3


def test_currency_signs_inside_words_name_no_currency():
    from statement_extractor.domain.basis import currencies_named
    assert currencies_named("(In millions of CA$)") == ["CAD"]
    assert currencies_named("A$ millions") == ["AUD"]
    assert currencies_named("Results of Operations$ in millions") == []
    assert currencies_named("the company won approval for the merger") == []
    assert currencies_named("(In millions of won)") == ["KRW"] and currencies_named("Won") == ["KRW"]
    assert currencies_named("(Won)456,063") == ["KRW"]


def test_a_footnote_mark_glued_to_a_figure_stays_off_it():
    assert number("975(a)") == 975.0 and number("1,234*") == 1234.0 and number("(21,836)(3)") == -21836.0
    assert number("1,234.5 (b)") == 1234.5
    assert number("12(a)") is None and number("2007(1)") is None  # a note reference, a year over its column
    assert figures("Other income   975(a)   1,234*") == ("Other income", [975.0, 1234.0])


def test_decimals_come_from_the_printed_text():
    from statement_extractor.domain.line_fields import printed_decimals
    from statement_extractor.readers.printed.tables import Cell, Row, printed_places
    row = Row(label="Diluted", level=0, figures={3: 1.9, 5: 1.95, 7: 242695.6},
              cells=[Cell(0, 1, "Diluted"), Cell(3, 1, "$ 1.90"), Cell(5, 1, "1.95"), Cell(7, 1, "242,695.6")])
    assert [printed_places(row, position) for position in (3, 5, 7, None)] == [2, 2, 1, None]
    assert printed_decimals(1.9, 1.0, 2) == 2 and printed_decimals(242695.6, 1e6, 1) == -5


def test_a_53_week_year_opens_the_day_after_the_year_before_it():
    from datetime import date, timedelta
    from statement_extractor.domain.periods import Period
    from statement_extractor.readers.printed.ranges import years_after_prior
    fy2003, fy2002 = date(2004, 1, 3), date(2002, 12, 28)
    periods = {(3,): Period(fy2003, fy2003 - timedelta(days=363)), (5,): Period(fy2002, fy2002 - timedelta(days=363))}
    years_after_prior(periods)
    assert periods[(3,)].start == date(2002, 12, 29) and periods[(5,)].start == date(2001, 12, 30)
    calendar_years = {(3,): Period(date(2005, 12, 31), date(2005, 1, 1)), (5,): Period(date(2004, 12, 31), date(2004, 1, 1))}
    years_after_prior(calendar_years)
    assert calendar_years[(3,)].start == date(2005, 1, 1)


def test_a_blank_printed_as_a_hyphen_and_a_won_sign_in_brackets():
    assert number("‐") == 0.0 and number("‒") == 0.0  # Ultrapar's U+2010 blanks
    assert number("(Won)456,063") == 456063.0 and number("(Won)(159,251)") == -159251.0  # KEPCO's 2005-06 20-Fs


def test_pence_are_a_hundredth_of_a_pound():
    assert number("2.9p") == 0.029 and number("(1.2)p") == -0.012
    from statement_extractor.readers.html.parse import _row
    from statement_extractor.readers.printed.tables import Cell
    row = _row([Cell(0, 1, "Basic earnings per share"), Cell(1, 1, "2.9"), Cell(2, 1, "p"), Cell(3, 1, "2.5"), Cell(4, 1, "p")])
    assert row.figures == {1: 0.029, 3: 0.025}
    from statement_extractor.readers.printed.tables import printed_places
    assert printed_places(row, 1) == 3


def test_per_basic_common_share_is_a_figure_a_share():
    from statement_extractor.domain.printed_labels import PER_SHARE
    assert PER_SHARE.search("Net earnings attributable to The Kroger Co. per basic common share")
    assert PER_SHARE.search("Net earnings attributable to The Kroger Co. per diluted common share")


def test_thousands_parted_by_dots_in_a_table_without_decimals():
    from statement_extractor.readers.html.cells import dotted_thousands
    from statement_extractor.readers.printed.tables import Cell

    def table(*rows):
        return [[Cell(index, 1, text) for index, text in enumerate(row)] for row in rows]

    def texts(lines):
        return [[cell.text for cell in cells] for cells in lines]
    # Eletropaulo's 2005 statements: dots throughout, nothing in the millions.
    assert texts(dotted_thousands(table(["Cash", "639.406", "512.003"], ["Receivables", "1.204.114", "998.250"][:1] + ["204.114", "998.250"])))[0] == ["Cash", "639,406", "512,003"]
    # A dot among commas: Azul's Q2 2017 "144.505".
    assert texts(dotted_thousands(table(["Passenger", "1,734,120", "144.505"], ["Cargo", "62,220", "51,004"])))[0][2] == "144,505"
    # A table printing decimals keeps its points; so does a per-share row.
    assert texts(dotted_thousands(table(["Revenue", "1,734", "144.505"], ["Exchange rate", "3.17", "3.10"])))[0][2] == "144.505"
    assert texts(dotted_thousands(table(["Revenue", "1,734", "1,500"], ["Dividends per share", "1.250", "1.120"])))[1][1] == "1.250"
    assert texts(dotted_thousands(table(["Cash", "639.406", "512.003"])))[0][1] == "639.406"  # two dotted figures prove nothing
    assert texts(dotted_thousands(table(["Passenger", "1,734,120", "144.505"], ["Loss per share", "(0.57)", "(0.40)"])))[0][2] == "144,505"


def test_a_row_of_dashes_is_never_a_sections_total():
    # Eletropaulo's 2002 Q2 and Petrobras's 2011 Q2 releases: an unlabelled "-" row after a section's unlabelled total.
    from datetime import date
    from statement_extractor.readers.html.read import read_document

    def row(*items):
        return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"
    html = ("<p>CONSOLIDATED STATEMENTS OF CASH FLOWS</p><p>(In thousands)</p><table>" + row("Year ended December 31,", "2005", "2004")
            + row("Operating activities", "", "") + row("Net income", "100", "90") + row("Depreciation", "50", "40")
            + row("", "150", "130") + row("", "-", "-")
            + row("Investing activities", "", "") + row("Capital expenditures", "(80)", "(70)") + row("", "(80)", "(70)")
            + row("Financing activities", "", "") + row("Dividends paid", "(20)", "(10)") + row("", "(20)", "(10)")
            + row("Net increase in cash", "50", "50") + row("Cash at beginning of year", "100", "50")
            + row("Cash at end of year", "150", "100") + "</table>")
    readings, _ = read_document(html, accession="a", form="10-K", filed=date(2006, 3, 1), document_end=date(2005, 12, 31),
                                fiscal_year_end="1231", year_offset=0)
    year = next(r for r in readings if r.statement == "cash_flow" and r.fiscal_year == "2005")
    assert {check.name: check.result for check in year.checks}.get("sections") != "fail" and year.status == "kept"


def test_a_percent_a_bracket_or_n_a_between_figures_is_no_second_statement():
    from statement_extractor.readers.html.parse import _side_by_side
    from statement_extractor.readers.printed.tables import Cell

    def lines(between):
        return [[Cell(0, 1, f"Line {index}"), Cell(2, 1, "1,000"), Cell(4, 1, "8.1"), Cell(5, 1, between),
                 Cell(6, 1, "900"), Cell(8, 1, "3.8"), Cell(9, 1, between), Cell(10, 1, "800")] for index in range(8)]
    for between in ("%", "(", "n.a.", "($"):
        assert _side_by_side(lines(between)) is None, between
    # Assets on the left and liabilities on the right of each row (TSMC) still part.
    paired = [[Cell(0, 1, f"Asset {index}"), Cell(2, 1, "1,000"), Cell(4, 1, "900"), Cell(6, 1, f"Liability {index}"),
               Cell(8, 1, "700"), Cell(10, 1, "600")] for index in range(8)]
    assert _side_by_side(paired) == 6


def test_a_year_dated_from_a_bare_year_keeps_its_counted_start():
    # Apple's "2005 | 2004": the years end on the fiscal year's usual day, a day off the printed ones; no week is missing.
    from datetime import date, timedelta
    from statement_extractor.domain.periods import Period
    from statement_extractor.readers.printed.ranges import years_after_prior
    periods = {(3,): Period(date(2005, 9, 24), date(2004, 9, 26)), (5,): Period(date(2004, 9, 24), date(2004, 9, 24) - timedelta(days=363))}
    years_after_prior(periods)
    assert periods[(3,)].start == date(2004, 9, 26)


def test_total_comprehensive_income_is_net_income_and_the_lines_under_its_heading():
    from statement_extractor.checks.printed import total_check
    printed = [("Revenues", 0, 100.0), ("Expenses", 0, -165.093), ("Net (loss) earnings", 0, -65.093),
               ("Preferred stock dividends", 0, -5.705), ("Net (loss) earnings available to common", 0, -70.798),
               ("Other comprehensive income (loss):", 0, None), ("Unrealized gain (loss) on investments", 2, -1.745),
               ("Total comprehensive (loss) income", 0, -66.838)]
    assert total_check(printed).result == "pass"
    printed[-1] = ("Total comprehensive (loss) income", 0, -60.0)
    assert total_check(printed).result == "fail"


def test_a_split_between_owners_is_no_step_to_the_groups_net_income():
    from statement_extractor.checks.printed import income_check
    figures = [("Income (loss) before income taxes", 0, -571.0), ("Current tax", 0, -127.0), ("Deferred tax", 0, 791.0),
               ("", 0, 664.0), ("Net income from continuing operations", 0, 93.0),
               ("Net income from continuing operations attributable to Vale's stockholders", 0, 86.0),
               ("Loss from discontinued operations", 0, -10.0), ("Net income attributable to noncontrolling interests", 0, 0.0),
               ("Loss from discontinued operations attributable to Vale's stockholders", 0, -10.0), ("Net income", 0, 83.0)]
    assert income_check(figures).result == "pass"


def test_months_to_date_named_beside_the_quarters():
    from datetime import date
    from statement_extractor.readers.printed.named_years import named_months
    from statement_extractor.readers.printed.tables import MONTHS_CELL
    assert named_months("6M19", "1231") == (date(2019, 6, 30), 6)  # Grupo Aeroportuario del Pacífico
    assert named_months("1H25", "1231") == (date(2025, 6, 30), 6)  # Azul
    assert named_months("9M20", "1231") == (date(2020, 9, 30), 9)
    assert named_months("YTD 11", "1231", header="3Q 11 % Rev 3Q 10 % Rev YTD 11 YTD 10") == (date(2011, 9, 30), 9)  # KOF
    assert named_months("YTD 11", "1231", header="YTD 11 YTD 10") is None  # no quarter to run to
    assert named_months("2H25", "1231") is None and named_months("2Q19", "1231") is None
    assert all(MONTHS_CELL.match(text) for text in ("6M19", "1H25", "YTD 11", "9M 2020"))
