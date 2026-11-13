"""A convenience translation's own heading never names the statement's currency or scale: KB Financial's and KT's
20-Fs, whose won statements were served as U.S. dollars, or a thousandth of themselves, until round 94."""
from datetime import date

from statement_extractor.readers.html.read import read_document


def row(*items):
    return "<tr>" + "".join(f"<td>{item}</td>" for item in items) + "</tr>"


def figures(cash, assets, liabilities, equity):
    return (row("Cash and cash equivalents", *cash) + row("Total assets", *assets) + row("Total liabilities", *liabilities)
            + row("Total equity", *equity) + row("Total liabilities and equity", *assets))


def read(html, end):
    readings, _notes = read_document(html, accession="a", form="20-F", filed=date(2030, 1, 1), document_end=end,
                                     fiscal_year_end="1231", year_offset=0)
    return {r.period_end.year: r for r in readings if r.statement == "balance"}


# KB's 2010 20-F: "Translation into U.S. dollars (Note 1) (In thousands)" printed before "(In millions of Korean won)".
KB_2010 = ("<p>KB FINANCIAL GROUP INC. AND SUBSIDIARIES CONSOLIDATED BALANCE SHEETS AS OF DECEMBER 31, 2009 and 2010</p>"
           "<table>" + row("", "2009", "2010", "2010") + row("Translation into U.S. dollars (Note 1) (In thousands)", "", "", "")
           + row("(In millions of Korean won)", "", "", "")
           + figures(("2,695,569", "3,867,316", "3,395,544"), ("253,855,432", "251,752,002", "221,140,028"),
                     ("235,000,000", "232,000,000", "203,794,000"), ("18,855,432", "19,752,002", "17,346,028")) + "</table>")

# KT's 2015 20-F: "(in millions of Korean won)" in the heading over its labels, the translation's "(in thousands of US
# dollars)" on the first row.
KT_2015 = ("<p>KT Corporation and Subsidiaries Consolidated Statements of Financial Position December 31, 2014 and 2015</p>"
           "<table>" + row("(in millions of Korean won)", "Notes", "2014", "2015", "2015")
           + row("(in thousands of US dollars)", "", "", "", "(Unaudited) (Note 2)")
           + "".join(row(label, "", *values) for label, *values in (
               ("Cash and cash equivalents", "1,888,663", "2,559,464", "2,181,782"),
               ("Total assets", "33,799,275", "29,404,725", "25,066,267"),
               ("Total liabilities", "21,000,000", "17,000,000", "14,491,620"),
               ("Total equity", "12,799,275", "12,404,725", "10,574,647"),
               ("Total liabilities and equity", "33,799,275", "29,404,725", "25,066,267"))) + "</table>")


def test_kb_reads_its_won_columns_in_millions():
    by_year = read(KB_2010, date(2010, 12, 31))
    assert sorted(by_year) == [2009, 2010]
    assert {r.currency for r in by_year.values()} == {"KRW"}
    assert by_year[2010].lines[0]["value"] == 3_867_316e6


def test_kt_reads_its_won_columns_as_won():
    by_year = read(KT_2015, date(2015, 12, 31))
    assert sorted(by_year) == [2014, 2015]
    assert {r.currency for r in by_year.values()} == {"KRW"}
    assert by_year[2015].lines[0]["value"] == 2_559_464e6


# SK Telecom's 2010 6-Ks: "Korean won | Translation into U.S. dollars (Note 2)" over "(In millions) | (In thousands)".
SKT_2010 = ("<p>SK TELECOM CO., LTD. NON-CONSOLIDATED STATEMENTS OF FINANCIAL POSITION JUNE 30, 2010 AND DECEMBER 31, 2009</p>"
            "<table>" + row("", "June 30,", "December 31,", "June 30,", "December 31,") + row("ASSETS", "2010", "2009", "2010", "2009")
            + row("", "Korean won", "", "Translation into U.S. dollars (Note 2)", "")
            + row("", "(In millions)", "", "(In thousands)", "")
            + figures(("292,307", "420,576", "247,722", "356,429"), ("19,158,117", "19,297,633", "16,235,692", "16,353,926"),
                      ("8,000,000", "8,100,000", "6,779,661", "6,864,407"), ("11,158,117", "11,197,633", "9,456,031", "9,489,519"))
            + "</table>")


def test_sk_telecom_reads_its_won_in_millions_where_no_scale_names_a_currency():
    by_year = read(SKT_2010, date(2010, 6, 30))
    cash = next(line["value"] for line in by_year[2010].lines if line["label"] == "Cash and cash equivalents")
    assert by_year[2010].currency == "KRW" and cash == 292_307e6


def test_a_company_name_ending_on_is_no_on_balance_sheet():
    from statement_extractor.readers.printed.find import TITLES, _titled
    balance = dict(TITLES)["balance"]
    assert _titled(balance, "MICROSOFT CORPORATION BALANCE SHEETS (In millions)")
    assert not _titled(balance, "off-balance sheet exposures") and not _titled(balance, "on balance sheet positions")


def test_the_group_first_under_each_date_is_no_parent_first_layout():
    from statement_extractor.readers.printed.side_by_side import SIDE_BY_SIDE
    assert SIDE_BY_SIDE.search("Company Consolidated Company Consolidated")  # Copel 2003: the parent's first
    assert not SIDE_BY_SIDE.search("September 30, 2002 June 30, 2002 Consolidated Company Consolidated Company")  # Copel 2002
