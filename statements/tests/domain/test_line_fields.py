from datetime import date

from statement_extractor.domain.line_fields import (fact_kind, finish_lines, places, printed_decimals, served_line,
                                                     taxonomy_of, unit_of)
from statement_extractor.domain.model import Reading
from statement_extractor.readers.xbrl.taxonomy import versions_in


def test_a_printed_figure_s_rounding_comes_from_its_scale():
    assert places(1234) == 0 and places(2.97) == 2 and places(0.5183) == 4
    assert printed_decimals(1234, 1e6) == -6  # $1,234 million
    assert printed_decimals(1234.5, 1e6) == -5
    assert printed_decimals(2.97, 1.0) == 2  # $2.97 a share
    assert printed_decimals(15_408, 1e3) == -3  # 15,408 thousand shares
    assert printed_decimals(None, 1e6) is None


def test_a_tagged_figure_s_kind_comes_from_its_fact_s_unit():
    assert fact_kind("usd", "USD", "us-gaap_Revenues", "Total net sales") == "money"
    assert fact_kind("usdPerShare", "USD", "us-gaap_EarningsPerShareBasic", "Basic") == "per_share"
    assert fact_kind("U_iso4217USD_xbrlishares", "USD", "aapl_Something", "Basic") == "per_share"
    assert fact_kind("shares", None, "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic", "Basic") == "shares"
    assert fact_kind("pure", None, "us-gaap_EffectiveIncomeTaxRatePercent", "Effective tax rate") == "pure"
    assert unit_of("per_share", "USD") == "USD/share" and unit_of("money", "TWD") == "TWD"
    assert unit_of("shares", None) == "shares" and unit_of("money", None) is None


def test_a_tag_s_taxonomy_takes_the_year_its_filing_imports():
    schema = ('<import namespace="http://fasb.org/us-gaap/2024" schemaLocation="https://xbrl.fasb.org/us-gaap/2024/elts/'
              'us-gaap-2024.xsd"/><import namespace="http://xbrl.sec.gov/dei/2024"/>'
              '<import namespace="http://xbrl.ifrs.org/taxonomy/2023-03-23/ifrs-full"/>')
    versions = versions_in(schema)
    assert versions == {"us-gaap": "us-gaap/2024", "dei": "dei/2024", "ifrs-full": "ifrs-full/2023"}
    assert versions_in('namespace="http://fasb.org/us-gaap/2009-01-31"') == {"us-gaap": "us-gaap/2009"}
    assert taxonomy_of("us-gaap_Revenues", versions) == "us-gaap/2024"
    assert taxonomy_of("aapl_ProductsRevenue", versions) == "aapl"  # the filer's own extension: no year
    assert taxonomy_of("printed_net_sales") is None and taxonomy_of("cvm_3.02") is None


def _reading(lines, currency="USD"):
    return Reading(statement="income", duration="year", fiscal_year="2024", fiscal_period="FY",
                   period_end=date(2024, 9, 28), period_start=date(2023, 10, 1), currency=currency,
                   accession="a", form="10-K", filed=date(2024, 11, 1), own=True, source="html", lines=lines)


def test_every_line_gets_a_unit_once_its_currency_is_known():
    reading = _reading([
        {"line": 0, "concept": "printed_revenues", "label": "Revenues", "level": 0, "is_heading": False,
         "value": 1.0e9, "unit_kind": "money", "decimals": -6},
        {"line": 1, "concept": "printed_per_share", "label": "Per share", "level": 0, "is_heading": True, "value": None},
        {"line": 2, "concept": "printed_basic", "label": "Basic", "level": 1, "is_heading": False, "value": 2.97,
         "unit_kind": "per_share", "decimals": 2},
        # A regulator's line, its kind from its account: CVM's 3.99 is earnings a share.
        {"line": 3, "concept": "cvm_3.99.01.01", "label": "ON", "level": 3, "is_heading": False, "value": 0.81},
        {"line": 4, "concept": "printed_shares", "label": "Weighted-average shares outstanding", "level": 0,
         "is_heading": False, "value": 15_408_095_000},
    ], currency="BRL")
    finish_lines([reading])
    assert [line["unit"] for line in reading.lines] == ["BRL", None, "BRL/share", "BRL/share", "shares"]
    assert all("unit_kind" not in line for line in reading.lines)
    assert reading.lines[0]["negated"] is False and reading.lines[4]["decimals"] is None
    served = served_line({**reading.lines[2], "code": "x", "fixed": True, "key": "printed_basic"})
    assert set(served) == {"line", "key", "concept", "taxonomy", "label", "level", "is_heading", "negated", "value",
                           "unit", "decimals"}
    assert served_line(reading.lines[1])["key"] == "printed_per_share"  # a heading keys by its own concept


def test_only_a_tagged_line_has_a_taxonomy():
    printed = _reading([{"line": 0, "concept": "us-gaap_NetIncomeLoss", "label": "Net income", "level": 0,
                         "is_heading": False, "value": 1.0, "unit_kind": "money"}])
    tagged = _reading([{"line": 0, "concept": "ifrs-full_Revenue", "label": "Revenue", "level": 0,
                        "is_heading": False, "value": 1.0}])
    tagged.source = "dart"
    finish_lines([printed, tagged])
    assert printed.lines[0]["taxonomy"] is None and tagged.lines[0]["taxonomy"] == "ifrs-full"
