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
