from datetime import date

import pytest

from kpi_extractor.ai import AiResponseError, Located, Spec, parse_located, parse_spec
from kpi_extractor.document import parse_document
from kpi_extractor.extract import check_period, read_values, validate_groups

RELEASE = """
<p>In the second quarter, shipments of 3-nanometer accounted for 30% of total wafer revenue; 5-nanometer accounted
for 33%; and 7-nanometer accounted for 11%.</p>
<p>DAP was 3.60 billion on average for June 2026.</p>
<p>Revenue by segment (in millions)</p>
<table>
  <tr><td></td><td colspan="2">Q2 2026</td><td colspan="2">Q2 2025</td></tr>
  <tr><td>Cloud</td><td>$</td><td>700</td><td>$</td><td>500</td></tr>
  <tr><td>Devices</td><td>$</td><td>300</td><td>$</td><td>250</td></tr>
  <tr><td>Total revenue</td><td>$</td><td>1,000</td><td>$</td><td>750</td></tr>
</table>
<p>Other nodes: 16nm 26%</p>
"""

SPEC = Spec.model_validate({"groups": [
    {"key": "technology", "label": "Revenue by technology", "kind": "mix",
     "kpis": [{"key": "n3", "label": "3nm", "unit": "percent"}, {"key": "n5", "label": "5nm", "unit": "percent"},
              {"key": "n7", "label": "7nm", "unit": "percent"}, {"key": "n16", "label": "16nm", "unit": "percent"}]},
    {"key": "segments", "label": "Revenue by segment", "kind": "revenue_breakdown", "total_kpi": "total",
     "kpis": [{"key": "cloud", "label": "Cloud", "unit": "currency"}, {"key": "devices", "label": "Devices", "unit": "currency"},
              {"key": "total", "label": "Total revenue", "unit": "currency"}]},
    {"key": "operating", "label": "Operating metrics", "kind": "metric",
     "kpis": [{"key": "dap", "label": "Daily active people", "unit": "count"}]},
]})


def _document():
    return parse_document(RELEASE, "https://example.com/ex99.htm")


def _block(document, needle):
    return next(key for key, block in document.blocks.items() if needle in block.text)


def _located(document, **overrides):
    nodes = _block(document, "3-nanometer")
    values = [
        {"kpi": "technology.n3", "block": nodes, "quote": "3-nanometer accounted for 30%", "value_text": "30%"},
        {"kpi": "technology.n5", "block": nodes, "quote": "5-nanometer accounted for 33%", "value_text": "33%"},
        {"kpi": "technology.n7", "block": nodes, "quote": "7-nanometer accounted for 11%", "value_text": "11%"},
        {"kpi": "technology.n16", "block": _block(document, "16nm"), "quote": "16nm 26%", "value_text": "26%"},
        {"kpi": "segments.cloud", "table": "T0", "row": 1, "col": 1, "scale": 1000000},
        {"kpi": "segments.devices", "table": "T0", "row": 2, "col": 1, "scale": 1000000},
        {"kpi": "segments.total", "table": "T0", "row": 3, "col": 1, "scale": 1000000},
        {"kpi": "operating.dap", "block": _block(document, "DAP"), "quote": "DAP was 3.60 billion", "value_text": "3.60 billion"},
    ]
    return Located.model_validate({"period_end": "2026-06-30", "values": values, **overrides})


def test_read_values_reads_numbers_from_the_document():
    document = _document()
    values, problems = read_values(document, SPEC, _located(document), "USD")
    assert problems == []
    read = {f"{item.group.key}.{item.kpi.key}": item.value for item in values}
    assert read == {"technology.n3": 30, "technology.n5": 33, "technology.n7": 11, "technology.n16": 26,
                    "segments.cloud": 700e6, "segments.devices": 300e6, "segments.total": 1000e6,
                    "operating.dap": 3.6e9}
    validate_groups(values, previous={})
    assert all(item.status == "verified" for item in values)


def test_invented_quotes_and_wrong_units_are_dropped():
    document = _document()
    located = Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "technology.n3", "block": _block(document, "3-nanometer"), "quote": "3-nanometer accounted for 31%", "value_text": "31%"},
        {"kpi": "technology.n5", "table": "T0", "row": 1, "col": 1, "scale": 1},
    ]})
    values, problems = read_values(document, SPEC, located, "USD")
    assert values == []
    assert any("quote not found" in problem for problem in problems)
    assert any("not a percentage" in problem for problem in problems)


def test_mix_that_does_not_add_up_needs_review():
    document = _document()
    values, _ = read_values(document, SPEC, _located(document), "USD")
    values = [item for item in values if item.kpi.key != "n16"]
    validate_groups(values, previous={})
    mix = [item for item in values if item.group.key == "technology"]
    assert all(item.status == "needs_review" for item in mix)
    assert "shares add up to 74.0%" in mix[0].notes


def test_breakdown_must_reconcile_to_its_total():
    document = _document()
    values, _ = read_values(document, SPEC, _located(document), "USD")
    values = [item for item in values if item.kpi.key != "devices"]
    validate_groups(values, previous={})
    assert all(item.status == "needs_review" for item in values if item.group.key == "segments")


def test_jumps_against_last_quarter_need_review():
    document = _document()
    values, _ = read_values(document, SPEC, _located(document), "USD")
    validate_groups(values, previous={"operating.dap": 0.9e9, "technology.n3": 70})
    status = {item.kpi.key: item.status for item in values}
    assert status["dap"] == "needs_review"
    assert status["n3"] == "needs_review"
    assert status["n5"] == "verified"


def test_check_period():
    assert check_period(Located(period_end="2026-06-28"), date(2026, 6, 30)) is None
    assert "does not match" in check_period(Located(period_end="2026-03-31"), date(2026, 6, 30))
    assert check_period(Located(period_end=None), date(2026, 6, 30)) == "reported period missing"


def test_ai_responses_are_validated():
    assert parse_spec('```json\n{"groups": []}\n```').groups == []
    with pytest.raises(AiResponseError):
        parse_spec('{"groups": [{"key": "Bad Key", "label": "x", "kind": "mix", "kpis": []}]}')
    with pytest.raises(AiResponseError):
        parse_located('{"values": [{"kpi": "a.b", "table": "T0", "row": 1}]}')
    with pytest.raises(AiResponseError):
        parse_spec('{"groups": [{"key": "mix", "label": "Mix", "kind": "mix", "kpis": [{"key": "a", "label": "A", "unit": "currency"}]}]}')


def test_a_quote_from_a_bullet_laid_out_as_a_table_is_read_as_text():
    # Meta's releases (2017–2020) set each highlight as a one-row table: "•" | the sentence. The AI quotes the
    # sentence and names the table as its block.
    document = parse_document("""
<p>Fourth Quarter and Full Year 2019 Operational and Other Financial Highlights</p>
<table><tr><td>•</td><td>Family daily active people (DAP) – DAP was 2.26 billion on average for December 2019, an increase of 11% year-over-year.</td></tr></table>
""", "https://example.com/ex99.htm")
    table = next(key for key, item in document.tables.items() if "DAP was" in " ".join(" ".join(row) for row in item.rows))
    spec = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating metrics", "kind": "metric",
                                             "kpis": [{"key": "dap", "label": "Daily active people", "unit": "count"}]}]})
    located = Located.model_validate({"period_end": "2019-12-31", "values": [
        {"kpi": "operating.dap", "block": table, "quote": "DAP was 2.26 billion on average for December 2019", "value_text": "2.26 billion"},
    ]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    assert [item.value for item in values] == [2_260_000_000]


def test_a_row_that_states_its_own_scale_overrides_the_tables():
    # JPM's supplement: a table "(in millions)" with "Debit and credit card sales volume (in billions)".
    document = parse_document("""
<p>(in millions)</p>
<table>
<tr><td>(in millions)</td><td>3Q26</td></tr>
<tr><td>Net revenue</td><td>18,300</td></tr>
<tr><td>Debit and credit card sales volume (in billions)</td><td>539.5</td></tr>
</table>""", "https://example.com/ex99.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "card", "label": "Card sales volume", "unit": "currency"}]}]})
    located = Located.model_validate({"period_end": "2026-09-30", "values": [
        {"kpi": "ops.card", "table": table, "row": 2, "col": 1, "scale": 1000000000}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    assert [item.value for item in values] == [539_500_000_000]
    assert values[0].notes == []



def test_a_share_is_never_read_as_an_amount():
    # TSMC's results-day slide: "28nm 7% 16/20nm 7% 0.15/0.18um 2%" once became $2 billion of 0.15 micron revenue.
    document = parse_document("<p>Revenue by technology (in billions): 28nm 7% 16/20nm 7% 0.15/0.18um 2%</p>", "https://example.com/x.htm")
    block = next(iter(document.blocks))
    spec = Spec.model_validate({"groups": [{"key": "tech", "label": "Revenue by technology", "kind": "metric",
                                             "kpis": [{"key": "old", "label": "0.15 micron and above", "unit": "currency"}]}]})
    located = Located.model_validate({"period_end": "2026-03-31", "values": [
        {"kpi": "tech.old", "block": block, "quote": "0.15/0.18um 2%", "value_text": "2%"}]})
    values, problems = read_values(document, spec, located, "TWD")
    assert values == []
    assert any("percentage, not an amount" in problem for problem in problems)
