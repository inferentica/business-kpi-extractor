"""Release tables that are not statement lines or not the company's statement (round 84)."""
from statement_extractor.domain.printed_labels import OPERATING_DATA
from statement_extractor.readers.printed.find import _NOT_IN_RELEASE, _NOT_STATEMENT


def test_operating_data_atop_an_income_statement_is_no_line():
    # Coca-Cola FEMSA's releases: served as millions of pesos until round 84 (39.9 pesos a unit case as 39,900,000).
    for label in ("Transactions (million transactions)", "Volume (million unit cases) (2)", "Average price per unit case (2)"):
        assert OPERATING_DATA.search(label)
    for label in ("Net revenues", "Total revenues (3)", "Cost of goods sold", "Net income per share"):
        assert not OPERATING_DATA.search(label)


def test_a_comparable_income_statement_is_not_the_companys():
    # Coca-Cola FEMSA's 1Q16 release prints its reported statement, then "Quarter - Comparable Income Statement (9)".
    title = "9 Quarter - Comparable Income Statement (9) Expressed in millions of Mexican pesos (1)"
    assert _NOT_IN_RELEASE.search(title) and _NOT_STATEMENT.search(title)
    assert not _NOT_IN_RELEASE.search("8 Quarter - Consolidated Income Statement Expressed in millions of Mexican pesos (1)")


def test_a_statement_titled_in_its_own_first_row_is_not_another_named_before_it():
    # Coca-Cola FEMSA's 4Q15 release: "This filing contains a detailed cash flow statement and selected notes to the
    # financial statements, including segment information. … February 23, 2016 Page 8", then a table whose first row is
    # "Consolidated Income Statement" (unread until round 85; the comparable statement was read in its place).
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    row = lambda *cells: "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"  # noqa: E731
    html = ("<p>Mexican Stock Exchange Quarterly Filing. This filing contains a detailed cash flow statement and selected "
            "notes to the financial statements, including segment information. February 23, 2016 Page 8</p><table>"
            + row("Consolidated Income Statement", "", "", "", "") + row("Expressed in millions of Mexican pesos", "", "", "", "")
            + row("", "4Q 15", "% Rev", "4Q 14", "% Rev")
            + "".join(row(*line) for line in (("Net revenues", "38,327", "", "34,759", ""), ("Total revenues", "38,433", "100%", "34,884", "100%"),
                                              ("Cost of goods sold", "20,168", "52.5%", "18,537", "53.1%"),
                                              ("Gross profit", "18,265", "47.5%", "16,347", "46.9%"),
                                              ("Net income", "2,861", "7.4%", "3,166", "9.1%"))) + "</table>")
    assert [kind for kind, _group in find_statements(parse_html(html), release=True)] == ["income"]


def test_femsas_2010_wordings():
    # FEMSA's FY2010 20-F and its 2010 releases (round 92): the Heineken exchange is money, series shares are per
    # share, "Net consolidated income" is net income.
    from statement_extractor.domain.printed_labels import NET_INCOME, PER_SHARE, SHARE_COUNT
    assert not SHARE_COUNT.search("Income from the exchange of shares with Heineken, net of taxes (Note 2)")
    assert SHARE_COUNT.search("Weighted average shares outstanding")
    assert PER_SHARE.search("Per series “B” share") and PER_SHARE.search("Per series “D”share")
    assert NET_INCOME.search("Net consolidated income")


def test_a_statement_ends_before_the_figures_after_it():
    from statement_extractor.readers.printed.tables import Row, statement_rows
    rows = [Row("Net consolidated income", 0, {1: 30476.0}), Row("Net majority income", 0, {1: 29216.0}),
            Row("EBITDA & CAPEX", 0), Row("EBITDA", 0, {1: 6716.0}), Row("FINANCIAL RATIOS", 0), Row("Liquidity", 0, {1: 1.53})]
    assert [row.label for row in statement_rows(rows)] == ["Net consolidated income", "Net majority income"]
    assert len(statement_rows([Row("EBITDA & CAPEX", 0), Row("EBITDA", 0, {1: 1.0})])) == 2  # nothing before it: no statement ended


def test_earnings_or_losses_per_share_and_per_thousand_shares_are_per_share():
    # Copel's "Earnings (loss) per thousand outstanding shares" (R$ 0.5183), Morgan Stanley's "Earnings (losses) per basic
    # common share", Apple's "Earnings (loss) per common and common equivalent share": served at the money scale to round 96.
    from statement_extractor.domain.printed_labels import PER_SHARE
    for label in ("Earnings (loss) per thousand outstanding shares", "Net income per thousand shares at year-end – in reais",
                  "Earnings (losses) per basic common share", "Earnings (loss) per common and common equivalent share"):
        assert PER_SHARE.search(label), label
    assert not PER_SHARE.search("Net income") and not PER_SHARE.search("Losses on shares of associates")
