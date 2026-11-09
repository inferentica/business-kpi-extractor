"""Each periodic report keeps what its figures are reported in: its accounting standard and reporting currency."""
import pandas as pd

from kpi_extractor.xbrl.basis import report_basis


def _facts(rows):
    return pd.DataFrame(rows, columns=["concept", "unit_ref", "currency"])


def test_a_us_gaap_report_in_dollars():
    facts = _facts([("dei:DocumentType", None, None), ("us-gaap:Revenues", "usd", "USD"),
                    ("aapl:ServicesNetSales", "usd", "USD"), ("us-gaap:EarningsPerShareBasic", "usdPerShare", "USD")])
    assert report_basis(facts) == ("US GAAP", "USD")


def test_an_ifrs_report_takes_its_revenue_currency_over_other_monetary_facts():
    # TSMC reports in New Taiwan dollars; a few facts (ADR figures) are in U.S. dollars.
    facts = _facts([("ifrs-full:Revenue", "TWD", "TWD"), ("tsmc:AdrDividends", "USD", "USD"),
                    ("tsmc:AdrPayments", "USD", "USD"), ("ifrs-full:ProfitLoss", "TWD", "TWD")])
    assert report_basis(facts) == ("IFRS", "TWD")


def test_a_generated_unit_id_is_read_through_its_resolved_currency():
    # SAP's unit ids are generated names; edgartools resolves each to its currency code.
    facts = _facts([("ifrs-full:Revenue", "Unit_Standard_EUR_DzninQPyI02xgP6HIeCj9A", "EUR")])
    assert report_basis(facts) == ("IFRS", "EUR")


def test_facts_that_do_not_say_give_nothing():
    assert report_basis(None) == (None, None)
    assert report_basis(_facts([("dei:EntityRegistrantName", None, None)])) == (None, None)
