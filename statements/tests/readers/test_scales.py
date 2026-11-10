from statement_extractor.readers.printed.scales import line_scale, share_scale, statement_scale
from statement_extractor.readers.printed.tables import Table


def table(context):
    return [Table(rows=[], header=[], context=context, index=0)]


def test_money_and_share_scales_are_read_apart():
    apple = table("CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except share amounts which are reflected in "
                  "thousands and per share amounts)")
    assert statement_scale(apple) == 1e6 and share_scale(apple, 1e6) == 1e3
    coke = table("(In millions except per share data)")
    assert statement_scale(coke) == 1e6 and share_scale(coke, 1e6) == 1e6
    units = table("(in thousands, except share and per share data)")
    assert statement_scale(units) == 1e3 and share_scale(units, 1e3) == 1.0
    intel = table("(In millions, except per share amounts; shares in thousands)")
    assert statement_scale(intel) == 1e6 and share_scale(intel, 1e6) == 1e3


def test_a_share_line_can_name_its_own_scale():
    assert line_scale("BASIC WEIGHTED AVERAGE SHARES OUTSTANDING (THOUSANDS)") == 1e3
    assert line_scale("", "Shares used in computing earnings per share (in thousands):") == 1e3
    assert line_scale("Average shares outstanding") is None
    assert line_scale("Shares Outstanding ('K)") == 1e3  # TSMC's 1999-2000 releases


def test_thousands_written_as_zeros():
    from statement_extractor.readers.printed.tables import Table
    for caption in ("($000, except stores and per share data, unaudited)", "(In $000s, except unit amounts)",
                    "(In 000s, except per unit amounts)", "($000's)", "(000 omitted)"):
        assert statement_scale([Table(rows=[], header=[], context="Consolidated Balance Sheets " + caption, index=0)]) == 1e3
    assert statement_scale([Table(rows=[], header=[], context="Revenue grew $1,000 in total (in millions)", index=0)]) == 1e6


def test_more_ways_of_stating_the_scale():
    from statement_extractor.readers.printed.tables import Table
    table = lambda text: [Table(rows=[], header=[], context=text, index=0)]
    assert statement_scale(table("CONSOLIDATED INCOME STATEMENT for the year ended 31 December Note 2014 £ million 2013 £ million")) == 1e6
    assert statement_scale(table("Consolidated Balance Sheets March 31 March 31 2009 2008 (Millions of US dollars)")) == 1e6
    assert statement_scale(table("Consolidated Statements of Operations (Dollar and share amounts in thousands, except per share data)")) == 1e3
    assert statement_scale(table("CONSOLIDATED STATEMENTS OF CASH FLOWS Won U.S. dollars (Note 2) 2006 2007 2008 2008 "
                                 "(In millions) (In thousands)")) == 1e6


def test_a_header_cell_that_is_the_unit_alone_is_the_scale():
    # Brascan's (Brookfield's) 2001 releases: "MILLIONS" over the label column, "MARCH 31 | December 31" over the figures.
    from statement_extractor.readers.text.parse import parse_text
    from statement_extractor.readers.printed.scales import statement_scale
    text = """<TABLE>
CONSOLIDATED BALANCE SHEET
                                                   MARCH 31   December 31
MILLIONS                                             2001        2000
<S>                                                <C>        <C>
  Cash and cash equivalents                         $   199     $   347
  Securities                                          2,348       2,371
                                                    $ 2,547     $ 2,718
</TABLE>"""
    assert statement_scale([parse_text(text)[0]]) == 1e6


def test_a_share_line_in_units_takes_no_scale():
    # Copel's CVM income statements, in thousands of reais: "NUMBER OF SHARES, EXCEPT TREASURY (Units)".
    assert line_scale("NUMBER OF SHARES, EXCEPT TREASURY (Units)") == 1.0


def test_a_currency_named_before_the_title_is_another_sections():
    # RPM's 1999 annual report: "EURO CURRENCY CONVERSION" in its MD&A, then a balance sheet "(In thousands...)".
    from statement_extractor.readers.printed.currency import reporting_currency
    rpm = table("EURO CURRENCY CONVERSION On January 1, 1999, eleven of the fifteen members of the European Union "
                "adopted the Euro as their common legal currency. " + "x " * 200 +
                "CONSOLIDATED BALANCE SHEETS RPM, Inc. and Subsidiaries (In thousands, except per share amounts)")
    assert reporting_currency(rpm, {}, "10-K") is None


def test_a_unit_row_over_the_currency_row_is_the_scale():
    # James Hardie's 2005 releases: "Millions of | Millions of" above "US Dollars | Australian Dollars".
    from statement_extractor.readers.printed.tables import Cell, Row
    rows = [Row(label="", level=0, cells=[Cell(1, 1, "Millions of"), Cell(2, 1, "Millions of")]),
            Row(label="Cash", level=0, figures={1: 10.0, 2: 13.0})]
    group = [Table(rows=rows, header=[[Cell(1, 1, "US Dollars"), Cell(2, 1, "Australian Dollars")]],
                   context="Consolidated Balance Sheets (Unaudited)", index=0)]
    assert statement_scale(group) == 1e6
