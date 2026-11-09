from datetime import date

from statement_extractor.readers.html.read import read_document


def row(*items):
    return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"


def balance(header: str) -> str:
    return ("<p>CONDENSED CONSOLIDATED BALANCE SHEET (UNAUDITED)</p><table>" + header
            + row("Cash and cash equivalents", "", "", "", "30", "", "", "", "20")
            + row("Investments", "", "", "", "70", "", "", "", "60")
            + row("Total assets", "", "", "", "100", "", "", "", "80")
            + row("Accounts payable", "", "", "", "40", "", "", "", "35")
            + row("Total equity", "", "", "", "60", "", "", "", "45")
            + row("Total liabilities and equity", "", "", "", "100", "", "", "", "80") + "</table>")


def ends(html: str, document_end: date) -> list[date]:
    readings, _notes = read_document(html, accession="x", form="6-K", filed=date(2023, 8, 10), document_end=document_end,
                                     fiscal_year_end="1231", year_offset=0)
    return sorted(r.period_end for r in readings if r.statement == "balance")


def test_one_header_day_for_each_column_in_order():
    # Brookfield's 6-Ks: "AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)" in the label cell, over "2023 | 2022".
    header = ('<tr><td colspan="3">AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)</td><td colspan="3">2023</td>'
              '<td colspan="3"></td><td colspan="3">2022</td></tr>')
    assert ends(balance(header), date(2023, 6, 30)) == [date(2022, 12, 31), date(2023, 6, 30)]


def test_a_bare_year_at_the_left_edge_heads_its_column():
    # Brookfield's income statements: the year row starts with "2023" in the first cell, over the quarter's figures.
    header = ('<tr><td colspan="3">AS AT JUNE 30, AND DECEMBER 31, (MILLIONS)</td></tr>'
              '<tr><td colspan="4">2023</td><td colspan="4"></td><td>2022</td></tr>')
    assert ends(balance(header), date(2023, 6, 30)) == [date(2022, 12, 31), date(2023, 6, 30)]


def test_each_year_takes_its_own_printed_date():
    # ASML's statutory interim report: "For the six-month period ended June 29, 2014 and June 30, 2013".
    header = ('<tr><td colspan="3">As at June 29, 2014 and June 30, 2013 (in millions)</td><td colspan="3">2014</td>'
              '<td colspan="3"></td><td colspan="3">2013</td></tr>')
    assert ends(balance(header), date(2014, 6, 29)) == [date(2013, 6, 30), date(2014, 6, 29)]
