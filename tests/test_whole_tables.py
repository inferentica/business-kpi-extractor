from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.document import parse_document
from kpi_extractor.extract import read_values, validate_groups

REPORT = """
<p>(Amounts in Thousands of New Taiwan Dollars)</p>
<p>Disaggregation of revenue</p>
<table>
  <tr><td></td><td colspan="2">Three Months Ended June 30</td></tr>
  <tr><td>Resolution</td><td>2026</td><td>2025</td></tr>
  <tr><td>2-nanometer</td><td>$ 31,934,248</td><td>$ -</td></tr>
  <tr><td>3-nanometer</td><td>320,558,574</td><td>187,252,107</td></tr>
  <tr><td>Advanced technologies (7-nanometer and below)</td><td>352,492,822</td><td>187,252,107</td></tr>
  <tr><td>5-nanometer</td><td>350,112,503</td><td>289,669,221</td></tr>
  <tr><td>Others</td><td>100,000,000</td><td>90,000,000</td></tr>
  <tr><td>Wafer revenue</td><td>$ 802,605,325</td><td>$ 566,921,328</td></tr>
</table>
<p>Information and Business Metrics (in millions, except employee data)</p>
<table>
  <tr><td></td><td>Q2 2026</td><td>Q2 2025</td></tr>
  <tr><td>Employees (full-time and part-time)</td><td>198,933</td><td>187,103</td></tr>
  <tr><td>Paid memberships (in millions)</td><td>310.2</td><td>290.1</td></tr>
</table>
"""

SPEC = Spec.model_validate({"groups": [
    {"key": "technology", "label": "Revenue by Technology", "kind": "revenue_breakdown", "total_kpi": "wafer",
     "kpis": [{"key": "n3", "label": "3nm", "unit": "currency"}, {"key": "wafer", "label": "Wafer revenue", "unit": "currency"}]},
    {"key": "operating", "label": "Operating Metrics", "kind": "metric",
     "kpis": [{"key": "employees", "label": "Employees", "unit": "count"},
              {"key": "members", "label": "Paid Memberships", "unit": "count"}]},
]})


def test_a_whole_table_brings_every_row_and_drops_subtotals():
    document = parse_document(REPORT, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    located = ai.Located.model_validate({"period_end": "2026-06-30", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 6, "total_row": 7}]})
    values, problems = read_values(document, SPEC, located, "TWD")
    assert problems == []
    parts = {item.kpi.label: item.value for item in values if not item.is_total}
    assert parts == {"2-nanometer": 31_934_248_000, "3-nanometer": 320_558_574_000,
                     "5-nanometer": 350_112_503_000, "Others": 100_000_000_000}
    assert all(item.kpi.key[0].isalpha() for item in values)
    validate_groups(values, previous={})
    assert all(item.status == "verified" for item in values)


def test_counts_ignore_the_tables_amount_scale_but_keep_their_own():
    document = parse_document(REPORT, "https://www.sec.gov/x.htm")
    table = list(document.tables)[1]
    located = ai.Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "operating.employees", "table": table, "row": 1, "col": 1, "scale": 1000000},
        {"kpi": "operating.members", "table": table, "row": 2, "col": 1},
    ]})
    values, problems = read_values(document, SPEC, located, "USD")
    read = {item.kpi.key: item.value for item in values}
    assert "employees" not in read  # a reading that scales a headcount into the billions is refused...
    assert any("plausible headcount" in problem for problem in problems)
    located.values[0].scale = 1
    values, _ = read_values(document, SPEC, located, "USD")
    read = {item.kpi.key: item.value for item in values}
    assert read["employees"] == 198_933  # ...but the table's "in millions" is never applied to a count
    assert read["members"] == 310_200_000


def test_documents_with_an_xml_declaration_parse():
    document = parse_document('<?xml version="1.0" encoding="utf-8"?><html><body><p>Revenue 1</p></body></html>',
                              "https://www.sec.gov/x.htm")
    assert any("Revenue 1" in block.text for block in document.blocks.values())


def test_an_audit_answer_with_one_malformed_group_keeps_the_rest():
    answer = ai.parse('{"add_groups": [{"key": "segments", "label": "Revenue by Segment", "kpis": []}, '
                      '{"key": "operating", "label": "Operating Metrics", "kind": "metric", '
                      '"kpis": [{"key": "wafers", "label": "Wafer Shipments", "unit": "count"}]}], '
                      '"add_kpis": {"technology": [{"key": "n2", "label": "2nm", "unit": "usd"}, '
                      '{"key": "n1", "label": "1.6nm", "unit": "currency"}]}}', ai.Maintenance)
    assert [group.key for group in answer.add_groups] == ["operating"]
    assert [kpi.key for kpi in answer.add_kpis["technology"]] == ["n1"]


def test_figures_quoted_from_text_take_their_blocks_scale():
    document = parse_document("<p>Three months ended (Unaudited, €, in millions) 2025 2026 Net system sales 5,596.1 "
                              "6,564.8 Total net sales 7,691.7 9,326.5</p>", "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [{"key": "product", "label": "Revenue by Product", "kind": "revenue_breakdown",
                                            "kpis": [{"key": "systems", "label": "Net System Sales", "unit": "currency"}]}]})
    block = next(iter(document.blocks))
    located = ai.Located.model_validate({"period_end": "2026-06-28", "values": [
        {"kpi": "product.systems", "block": block, "quote": "Net system sales 5,596.1 6,564.8", "value_text": "6,564.8"}]})
    values, problems = read_values(document, spec, located, "EUR")  # the company's XBRL currency
    assert problems == [] and values[0].value == 6_564_800_000 and values[0].currency == "EUR"


def test_a_bare_dollar_sign_follows_the_reports_declared_currency():
    document = parse_document(REPORT, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    located = ai.Located.model_validate({"period_end": "2026-06-30", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 6, "total_row": 7}]})
    values, _ = read_values(document, SPEC, located, "USD")  # even when told the company reports in dollars
    assert {item.currency for item in values} == {"TWD"}


def test_a_passing_mention_of_a_currency_is_not_a_declaration():
    from kpi_extractor.document import declared_currency
    assert declared_currency("Sales to Europe are billed in euros. (In millions) Revenue $ 1,000") is None
    assert declared_currency("(Unaudited, €, in millions, except per share data)") == "EUR"


def test_only_the_rows_a_total_covers_are_kept():
    document = parse_document("""<p>(In millions)</p><table>
      <tr><td></td><td>Three Months Ended June 27, 2026</td></tr>
      <tr><td>Data Center</td><td>$ 4,000</td></tr>
      <tr><td>Client</td><td>2,500</td></tr>
      <tr><td>Gaming</td><td>1,100</td></tr>
      <tr><td>Client and Gaming</td><td>3,600</td></tr>
      <tr><td>Embedded</td><td>900</td></tr>
    </table>""", "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [{"key": "cg", "label": "Client and Gaming", "kind": "revenue_breakdown",
                                            "total_kpi": "total", "kpis": [
        {"key": "client", "label": "Client", "unit": "currency"}, {"key": "gaming", "label": "Gaming", "unit": "currency"},
        {"key": "total", "label": "Client and Gaming", "unit": "currency"}]}]})
    table = next(iter(document.tables))
    located = ai.Located.model_validate({"period_end": "2026-06-27", "tables": [
        {"group": "cg", "table": table, "col": 1, "first_row": 1, "last_row": 5, "total_row": 4}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    assert {item.kpi.label: item.value for item in values if not item.is_total} == {"Client": 2.5e9, "Gaming": 1.1e9}
