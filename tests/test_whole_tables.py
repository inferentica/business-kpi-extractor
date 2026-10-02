from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.document import parse_document
from kpi_extractor.ai import KpiSpec
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
    assert parts == {"2-nanometer": 31_934_248_000, "3nm": 320_558_574_000,  # a listed KPI keeps its listed name
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


def test_table_rows_take_the_listed_kpi_keys():
    from kpi_extractor.extract import _listed_kpi
    group = SPEC.groups[0]
    assert _listed_kpi(group, "3-nanometer").key == "n3"
    listed = Spec.model_validate({"groups": [{"key": "p", "label": "Revenue by Product Type", "kind": "revenue_breakdown",
                                              "total_kpi": "total", "kpis": [
        {"key": "net_product_sales", "label": "Net product sales", "unit": "currency"},
        {"key": "nm3", "label": "3nm", "unit": "currency"},
        {"key": "total", "label": "Total net sales", "unit": "currency"}]}]}).groups[0]
    assert _listed_kpi(listed, "Net product sales").key == "net_product_sales"
    assert _listed_kpi(listed, "3-nanometer").key == "nm3"
    assert _listed_kpi(listed, "Net service sales") is None


def test_every_reading_refuses_a_column_headed_for_another_period():
    from kpi_extractor.extract import column_duration_problem
    assert column_duration_problem("<6 months> ended @ # #", annual=False)
    assert column_duration_problem("six months ended @ #", annual=False)
    assert column_duration_problem("three months ended @ #", annual=True)
    assert column_duration_problem("three months ended @ # six months ended", annual=False) is None
    assert column_duration_problem("# #", annual=False) is None
    six = REPORT.replace("Three Months Ended June 30", "Six Months Ended June 30")
    document = parse_document(six, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    located = ai.Located.model_validate({"period_end": "2026-06-30", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 6, "total_row": 7}]})
    values, problems = read_values(document, SPEC, located, "TWD")
    assert values == [] and any("not a quarter" in problem for problem in problems)


def test_a_table_split_by_a_page_break_is_read_to_its_total():
    html = """<p>(Amounts in Thousands of New Taiwan Dollars)</p><table>
      <tr><td></td><td>Three Months Ended June 30</td><td>Six Months Ended June 30</td></tr>
      <tr><td>Resolution</td><td>2023</td><td>2023</td></tr>
      <tr><td>3-nanometer</td><td>$483,710</td><td>$483,710</td></tr>
      <tr><td>5-nanometer</td><td>127,824,564</td><td>267,120,041</td></tr></table>
      <p>(Continued)</p><table>
      <tr><td></td><td>Three Months Ended June 30</td><td>Six Months Ended June 30</td></tr>
      <tr><td>Resolution</td><td>2023</td><td>2023</td></tr>
      <tr><td>28-nanometer</td><td>$47,590,123</td><td>$99,647,165</td></tr>
      <tr><td>Wafer revenue</td><td>$175,898,397</td><td>$367,250,916</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    first = next(iter(document.tables))
    located = ai.Located.model_validate({"period_end": "2023-06-30", "tables": [
        {"group": "technology", "table": first, "col": 1, "first_row": 2, "last_row": 3}]})
    values, problems = read_values(document, SPEC, located, "TWD")
    assert problems == []
    assert {item.kpi.label: item.value for item in values if not item.is_total}["28-nanometer"] == 47_590_123_000
    assert [item.value for item in values if item.is_total] == [175_898_397_000]


def test_a_third_quarter_table_carries_its_nine_months():
    html = """<p>(Amounts in Thousands of New Taiwan Dollars)</p><table>
      <tr><td></td><td>Three Months Ended September 30</td><td>Nine Months Ended September 30</td></tr>
      <tr><td>Resolution</td><td>2023</td><td>2023</td></tr>
      <tr><td>3-nanometer</td><td>$28,994,752</td><td>$29,478,462</td></tr>
      <tr><td>5-nanometer</td><td>173,190,000</td><td>440,310,041</td></tr>
      <tr><td>Wafer revenue</td><td>$202,184,752</td><td>$469,788,503</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    located = ai.Located.model_validate({"period_end": "2023-09-30", "tables": [
        {"group": "technology", "table": next(iter(document.tables)), "col": 1, "first_row": 2, "last_row": 3}]})
    values, _ = read_values(document, SPEC, located, "TWD")
    five = next(item for item in values if item.kpi.label == "5-nanometer")
    assert five.locator["ytd"] == 440_310_041_000 and five.locator["ytd_total"] == 469_788_503_000


def test_a_node_ramping_from_an_immaterial_base_is_not_a_jump():
    from kpi_extractor.extract import ReadValue
    group = SPEC.groups[0]
    items = [ReadValue(group, group.kpis[0], 29.0, "TWD", {}), ReadValue(group, group.kpis[1], 444.0, "TWD", {}, is_total=True),
             ReadValue(group, KpiSpec(key="n5", label="5nm", unit="currency"), 415.0, "TWD", {})]
    validate_groups(items, previous={"technology.n3": 0.5, "technology.n5": 420.0})
    assert all(item.status == "verified" for item in items)


def test_a_mix_total_row_is_not_a_share_and_q2_headers_are_quarters():
    from kpi_extractor.extract import column_duration_problem
    assert column_duration_problem("q2 fiscal year #", annual=False) is None
    html = """<table><tr><td></td><td>2Q26</td></tr><tr><td>3nm</td><td>30%</td></tr><tr><td>5nm</td><td>35%</td></tr>
      <tr><td>Others</td><td>35%</td></tr><tr><td>Total</td><td>100%</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [{"key": "mix", "label": "Wafer Revenue by Technology", "kind": "mix",
                                            "kpis": [{"key": "n3", "label": "3nm", "unit": "percent"}]}]})
    located = ai.Located.model_validate({"period_end": "2026-06-30", "tables": [
        {"group": "mix", "table": next(iter(document.tables)), "col": 1, "first_row": 1, "last_row": 4}]})
    values, problems = read_values(document, spec, located, None)
    assert problems == [] and sum(item.value for item in values if not item.is_total) == 100


def test_a_row_label_carrying_the_business_name_takes_the_listed_key():
    from kpi_extractor.extract import _listed_kpi
    group = Spec.model_validate({"groups": [{"key": "customers", "label": "Revenue by Customer Type", "kind": "revenue_breakdown",
                                             "kpis": [{"key": "domestic", "label": "Employer & Individual - Domestic", "unit": "currency"},
                                                      {"key": "global", "label": "Employer & Individual - Global", "unit": "currency"},
                                                      {"key": "other", "label": "Other", "unit": "currency"}]}]}).groups[0]
    assert _listed_kpi(group, "UnitedHealthcare Employer & Individual - Domestic").key == "domestic"
    assert _listed_kpi(group, "UnitedHealthcare Employer & Individual - Total") is None
    assert _listed_kpi(group, "Another") is None  # a short name is never matched by its ending


def test_a_combined_line_never_takes_the_listed_key_of_its_last_part():
    from kpi_extractor.extract import _listed_kpi
    group = Spec.model_validate({"groups": [{"key": "cib", "label": "CIB Revenue by Business", "kind": "revenue_breakdown",
                                             "kpis": [{"key": "securities", "label": "Securities Services", "unit": "currency"},
                                                      {"key": "markets", "label": "Fixed Income Markets", "unit": "currency"}]}]}).groups[0]
    assert _listed_kpi(group, "Markets & Securities Services") is None
    assert _listed_kpi(group, "JPM Securities Services").key == "securities"


def test_footnote_markers_run_into_a_label_are_dropped():
    from kpi_extractor.extract import _row_label
    assert _row_label(["Banking & Wealth Management14", "1"], 1) == "Banking & Wealth Management"
    assert _row_label(["Google subscriptions, platforms, and devices(1)", "1"], 1) == "Google subscriptions, platforms, and devices"
    assert _row_label(["Q4", "1"], 1) == "Q4"


def test_a_pointer_at_a_combined_line_is_rejected():
    from kpi_extractor.extract import _combined_line
    assert _combined_line("Total Markets & Securities Services", "Securities Services")
    assert _combined_line("Markets & Securities Services", "Securities Services")
    assert not _combined_line("Securities Services", "Securities Services")
    assert not _combined_line("UnitedHealthcare Employer & Individual - Domestic", "Employer & Individual - Domestic")
