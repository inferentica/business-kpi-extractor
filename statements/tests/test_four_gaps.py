"""Lloyds, PicS, Alcon and UBS gaps (round 89): each rule on the layout that needed it."""
from datetime import date

from statement_extractor.domain.periods import Period
from statement_extractor.domain.printed_labels import BEFORE_FINANCING, LIABILITIES_AND_EQUITY, NET_INCOME


def lines(*pairs):
    return [(label, 0, value) for label, value in pairs]


def test_net_income_and_grand_total_wordings():
    assert NET_INCOME.search("Net (loss)/income")  # Alcon 2019-20
    assert NET_INCOME.search("(Loss) profit for the period")  # Lloyds 2011
    assert LIABILITIES_AND_EQUITY.search("TOTAL EQUITY AND LIABILITIES")  # PicS
    assert BEFORE_FINANCING.match("Net cash inflow (outflow) before financing")  # Lloyds TSB, FRS 1


def test_an_frs1_cash_flow_is_proven_by_its_sections():
    from statement_extractor.checks.cash import cash_check
    figures = lines(("Net cash inflow from operating activities", 4670), ("Dividends received from associated undertakings", 5),
                    ("Payments made to non-equity minority interests", -40), ("Interest paid on subordinated liabilities", -297),
                    ("Net cash outflow from returns on investments and servicing of finance", -337),
                    ("UK corporation tax", -205), ("Overseas tax", -119), ("Total taxation", -324),
                    ("Additions to fixed asset investments", -19519), ("Disposals of fixed asset investments", 18656),
                    ("Additions to tangible fixed assets", -346), ("Disposals of tangible fixed assets", 154),
                    ("Net cash outflow from capital expenditure and financial investment", -1055),
                    ("Equity dividends paid", -1311), ("Net cash inflow (outflow) before financing", 1648),
                    ("Issue of subordinated liabilities (loan capital)", 532), ("Repayments of subordinated liabilities", -29),
                    ("Net cash inflow from financing", 503), ("Increase (decrease) in cash", 2151))
    assert cash_check(figures).result == "pass"
    wrong = [*figures[:-1], ("Increase (decrease) in cash", 0, 2000)]
    assert cash_check(wrong).result == "fail"


def test_a_cash_roll_forward_allows_half_a_unit_for_each_figure():
    from statement_extractor.checks.cash import cash_check
    # UBS's Q3 2022: 207,875 - 2,382 - 15,788 = 189,705 against 189,707 printed, four figures.
    figures = lines(("Net cash flow from / (used in) operating activities", 15000), ("Net cash flow from / (used in) investing activities", -8443),
                    ("Net cash flow from / (used in) financing activities", -8939),
                    ("Cash and cash equivalents at the beginning of the period", 207875),
                    ("Net cash flow from / (used in) operating, investing and financing activities", -2382),
                    ("Effects of exchange rate differences on cash and cash equivalents", -15788),
                    ("Cash and cash equivalents at the end of the period", 189707))
    assert cash_check(figures).result == "pass"


def test_totals_over_totals_over_their_parts():
    from statement_extractor.checks.printed import total_check
    # PicS: "Financial assets" over three groups, one of them a single line of its own figure.
    printed = lines(("Cash and cash equivalents", 5_478_020), ("Financial assets", 36_031_913), ("At fair value through profit or loss", 73_069),
                    ("Financial investments", 43_903), ("Derivative financial instruments", 29_166),
                    ("At fair value through other comprehensive income", 2_873_043), ("Financial Investments", 2_873_043),
                    ("At amortized cost", 33_085_800), ("Trade receivables", 3_903_868), ("Consumer loans", 29_181_932),
                    ("Intangible assets", 1_187_678), ("TOTAL ASSETS", 42_697_611))
    assert total_check(printed).result == "pass"


def test_a_columns_own_year_to_date_heading():
    from statement_extractor.readers.printed.ranges import YEAR_TO_DATE
    assert YEAR_TO_DATE.search("Year-to-date 30.6.25") and not YEAR_TO_DATE.search("For the quarter ended 30.6.25")


def test_pages_after_an_inline_xbrl_header_are_positioned():
    from statement_extractor.readers.positioned.layout import is_positioned
    header = "<ix:header>" + "<xbrli:context id='c'>x</xbrli:context>" * 20000 + "</ix:header>"
    page = '<div id="Page{0}" style="position:relative;">' + '<div style="position:absolute;left:1px;top:2px;">a</div>' * 120 + "</div>"
    assert is_positioned("<html><body>" + header + "".join(page.format(n) for n in range(4)) + "</body></html>")


def test_off_and_on_balance_sheet_and_about_name_no_balance_sheet():
    from statement_extractor.readers.printed.find import TITLES, _titled
    pattern = dict(TITLES)["balance"]
    assert not _titled(pattern, "the maximum exposure to credit risk for off-balance sheet financial instruments")
    assert not _titled(pattern, "Gross carrying amount (USD million) On-balance sheet")
    assert not _titled(pattern, "for more information about balance sheet movements 40")
    assert _titled(pattern, "UBS Group AG Balance sheet")


def test_a_statement_dated_only_to_earlier_periods_prints_none_of_the_documents():
    from statement_extractor.readers.printed.titled import titled_notes
    # Lloyds TSB's June 2003 6-K: its 2002 halves restated.
    earlier = {(1,): Period(date(2002, 6, 30), date(2002, 1, 1)), (2,): Period(date(2002, 12, 31), date(2002, 7, 1))}
    assert titled_notes("income", earlier, date(2003, 3, 31), "1231", 0) == []
    assert titled_notes("income", {}, date(2003, 3, 31), "1231", 0) == ["titled: income"]


def test_non_statutory_bases_are_not_the_companys_statements():
    from statement_extractor.readers.printed.find import _NOT_IN_RELEASE, _UNDERLYING
    assert _NOT_IN_RELEASE.search("2005 summary income statement on an EEV basis")
    assert _UNDERLYING.search("Combined businesses consolidated income statement")


def test_quarters_when_the_company_was_no_registrant():
    from statement_extractor.quality.coverage import _registrant_gap
    alcon = [{"form": "6-K", "release_quarter": ["2011", "Q1"], "filed": "2011-04-07"},
             {"form": "6-K", "release_quarter": ["2018", "Q4"], "filed": "2019-04-02"}]
    gap = _registrant_gap(alcon, [("2011", "Q1"), ("2018", "Q4")])
    assert ("2011", "Q2") in gap and ("2018", "Q3") in gap and ("2011", "Q1") not in gap and ("2018", "Q4") not in gap
    pics = [{"form": "6-K", "release_quarter": ["2025", "Q4"], "filed": "2026-03-19"}, {"form": "20-F", "filed": "2026-04-30"}]
    assert _registrant_gap(pics, [("2025", "Q4")]) == {("2025", "Q1"), ("2025", "Q2"), ("2025", "Q3")}
    copel = [{"form": "20-F", "filed": "2002-07-15"}, {"form": "6-K", "release_quarter": ["2002", "Q3"], "filed": "2002-11-19"}]
    assert _registrant_gap(copel, [("2002", "Q3")]) == set()  # filing 20-Fs on EDGAR, its 6-Ks on paper


def test_a_quarters_own_report_in_the_currency_before_its_change():
    from statement_extractor.domain.model import Reading
    from statement_extractor.selection.choose import choose
    def reading(year, period, currency, form="6-K", own=True, end=date(2018, 3, 31)):
        return Reading(statement="balance", duration="instant", fiscal_year=year, fiscal_period=period, period_end=end,
                       period_start=None, currency=currency, accession=f"{form}{year}{currency}", form=form,
                       filed=date(int(year) + 1, 3, 1) if form == "20-F" else date(2018, 4, 23), own=own, source="html",
                       lines=[{"line": 0, "concept": "printed", "label": "Total assets", "level": 0, "is_heading": False,
                               "value": 1.0}], checks=[], basis="IFRS", scope="consolidated")
    readings = [reading("2017", "FY", "CHF", "20-F", end=date(2017, 12, 31)), reading("2018", "FY", "USD", "20-F", end=date(2018, 12, 31)),
                reading("2018", "Q1", "CHF")]
    for item in readings:
        from statement_extractor.domain.model import Check
        item.checks.append(Check("balance", "pass"))
    chosen = {(record["fiscal_year"], record["fiscal_period"]): record for record in choose(readings)}
    assert chosen[("2018", "Q1")]["status"] == "kept"  # UBS's Q1 2018 report, in francs before its move to dollars


def test_a_quarter_heading_over_its_currency_and_translation():
    # TSMC's archived Q1 1999 cash flow: "1Q  1999" over USD and NTD, then "1Q  1998" and "4Q  1998" (4Q 1998 read as
    # the year 1998, the 1999 NTD column set aside with the dollars, until round 89).
    from statement_extractor.readers.text.read import read_document
    text = ("                     UNCONSOLIDATED STATEMENT OF CASH FLOWS\n"
            "                  (Figures in Million of New Taiwan dollars (NTD) and U.S. dollars (USD))\n"
            + " " * 69 + "1Q  1999          1Q  1998     4Q  1998\n"
            + " " * 64 + "USD         NTD          NTD          NTD\n"
            "      Cash Flows from Operating Activities:\n"
            "          Net Income                                                126       4,090        6,947        2,524\n"
            "          Net Cash Provided from Operating Activities               230       7,495       11,181        9,652\n"
            "          Net Cash Used in Investing Activities                    -145      -4,726       -9,027       -3,448\n"
            "          Net Cash Provided by Financing Activities                  22         730        1,144       -1,666\n"
            "      Net Increase (Decrease) in Cash and Cash Equivalents          107       3,499        3,299        4,537\n"
            "      Cash and Cash Equivalents at Beginning of Period              251       8,292        2,534        3,755\n"
            "      Cash and Cash Equivalents at End of Period                    357      11,792        5,833        8,292\n")
    readings, _notes = read_document(text, accession="x", form="6-K", filed=date(1999, 4, 30), document_end=date(1999, 3, 31),
                                     fiscal_year_end="1231", year_offset=0)
    assert sorted((r.period_start, r.period_end) for r in readings) == [
        (date(1998, 1, 1), date(1998, 3, 31)), (date(1998, 10, 1), date(1998, 12, 31)), (date(1999, 1, 1), date(1999, 3, 31))]
