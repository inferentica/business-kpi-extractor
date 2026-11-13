from datetime import date

import pytest

from kpi_extractor.ai import AiResponseError, Located, Spec, parse_located, parse_spec
from kpi_extractor.documents.document import parse_document
from kpi_extractor.quality.checks import check_period, validate_groups
from kpi_extractor.readers.extract import listed_after_fall, read_values

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


def test_a_jump_does_not_hold_a_figure_its_quote_states():
    # Owner, 2026-11-09: a figure is judged by its filing, not by how far it moved. Both jump against last quarter,
    # and both quotes state them ("DAP was 3.60 billion", "3-nanometer accounted for 30%"), so both stand.
    document = _document()
    values, _ = read_values(document, SPEC, _located(document), "USD")
    validate_groups(values, previous={"operating.dap": 0.9e9, "technology.n3": 70})
    status = {item.kpi.key: item.status for item in values}
    assert status["dap"] == "verified"
    assert status["n3"] == "verified"
    assert status["n5"] == "verified"


def test_a_jump_still_holds_a_figure_its_filing_does_not_state():
    document = _document()
    values, _ = read_values(document, SPEC, _located(document), "USD")
    for item in values:
        item.locator = {}  # nothing in the filing shown for it
    validate_groups(values, previous={"operating.dap": 0.9e9})
    assert {item.kpi.key: item.status for item in values}["dap"] == "needs_review"


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


def test_a_row_in_trillions_overrides_a_table_in_millions():
    # JPM's supplement, Q3 2025: "Total payments transaction volume (in trillions)" 1.8 in a table "(in millions)".
    document = parse_document("""
<table>
<tr><td>(in millions)</td><td>3Q25</td></tr>
<tr><td>Net revenue</td><td>18,300</td></tr>
<tr><td>Total payments transaction volume (in trillions)</td><td>1.8</td></tr>
</table>""", "https://example.com/ex99.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "tpv", "label": "Total payments transaction volume", "unit": "currency"}]}]})
    located = Located.model_validate({"period_end": "2025-09-30", "values": [
        {"kpi": "ops.tpv", "table": table, "row": 2, "col": 1, "scale": 1e12}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == [] and [item.value for item in values] == [1.8e12] and values[0].notes == []



def test_a_count_read_from_text_takes_its_labels_scale():
    # SpaceX's Q2 2026 release, its table flattened into text: "Starlink Subscribers (#, in millions, ...) 12.0" was 12.
    document = parse_document("<p>Starlink Subscribers (#, in millions, as of period end) 12.0 10.3 6.0</p>", "https://example.com/x.htm")
    block = next(iter(document.blocks))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "subs", "label": "Starlink subscribers", "unit": "count"}]}]})
    located = Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "ops.subs", "block": block, "quote": "Starlink Subscribers (#, in millions, as of period end) 12.0", "value_text": "12.0"}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    assert [item.value for item in values] == [12_000_000]


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


def _nested_spec():
    return Spec.model_validate({"groups": [{"key": "segments", "label": "Revenue by segment", "kind": "revenue_breakdown",
                                             "total_kpi": "total", "kpis": [
        {"key": "uhc", "label": "UnitedHealthcare", "unit": "currency"},
        {"key": "optum", "label": "Total Optum", "unit": "currency"},
        {"key": "health", "label": "Optum Health", "unit": "currency", "parent": "optum"},
        {"key": "rx", "label": "Optum Rx", "unit": "currency", "parent": "optum"},
        {"key": "total", "label": "Total revenues", "unit": "currency"}]}]})


def test_a_parent_and_its_lines_are_checked_each_at_their_level():
    from kpi_extractor.readers.extract import ReadValue

    group = _nested_spec().groups[0]
    kpi = {k.key: k for k in group.kpis}
    read = lambda values: [ReadValue(group, kpi[key], value, "USD", {}) for key, value in values.items()]
    good = read({"uhc": 60.0, "optum": 40.0, "health": 25.0, "rx": 15.0, "total": 100.0})
    validate_groups(good, {})
    assert all(item.status == "verified" for item in good)  # Optum counted once, its lines against it
    off = read({"uhc": 60.0, "optum": 40.0, "health": 25.0, "rx": 10.0, "total": 100.0})
    validate_groups(off, {})
    assert {item.kpi.key for item in off if item.status == "needs_review"} == {"optum", "health", "rx"}
    assert any("lines differ from Total Optum" in note for item in off for note in item.notes)
    # Without the parent this quarter, its lines stand in for it.
    alone = read({"uhc": 60.0, "health": 25.0, "rx": 15.0, "total": 100.0})
    validate_groups(alone, {})
    assert all(item.status == "verified" for item in alone)


def test_a_parent_must_be_another_kpi_of_the_group_and_never_loop():
    spec = _nested_spec().model_dump()
    kpis = spec["groups"][0]["kpis"]
    kpis[2]["parent"] = "missing"
    kpis[1]["parent"] = "rx"  # Total Optum under its own line: a loop
    group = Spec.model_validate(spec).groups[0]
    assert {kpi.key: kpi.parent for kpi in group.kpis} == {"uhc": None, "optum": None, "health": None, "rx": "optum", "total": None}


def test_a_row_with_a_footnote_mark_set_apart_is_its_listed_kpi():
    # ASML's Q1 2017 release: "Net system sales 3" was stored again as a KPI of its own, doubling the quarter.
    from kpi_extractor.ai import GroupSpec
    from kpi_extractor.readers.extract import _listed_kpi
    group = GroupSpec.model_validate({"key": "product", "label": "Revenue by product", "kind": "revenue_breakdown", "kpis": [
        {"key": "net_system_sales", "label": "Net system sales", "unit": "currency"}]})
    assert _listed_kpi(group, "Net system sales 3").key == "net_system_sales"
    assert _listed_kpi(group, "Something else 3") is None


def test_a_6k_read_from_its_own_document_is_classified_from_the_report_after_its_cover():
    # TSMC's 6-Ks until October 2019: the cover and signature, then the release itself.
    from kpi_extractor.sources.sec import _after_cover
    text = ("FORM 6-K REPORT OF FOREIGN PRIVATE ISSUER ... Indicate by check mark whether the registrant files annual reports "
            "under cover of Form 20-F ... SIGNATURES Pursuant to the requirements of the Securities Exchange Act of 1934 ... "
            "By /s/ Lora Ho Lora Ho Senior Vice President & Chief Financial Officer TSMC Reports Third Quarter EPS of NT$3.44")
    assert text[_after_cover(text):].strip().startswith("TSMC Reports Third Quarter")
    assert _after_cover("A release with no cover") == 0


def test_a_cover_written_with_html_spaces_is_still_skipped():
    from types import SimpleNamespace
    from kpi_extractor.sources.sec import FilingRef, classification_excerpt
    cover = "FORM&nbsp;6-K ... SIGNATURES ... By&nbsp;/s/&nbsp;Lora Ho Lora Ho Senior Vice President &amp; Chief Financial Officer"
    exhibit = SimpleNamespace(document_type="6-K", document="d6k.htm", description="FORM 6-K", content=f"<p>{cover}</p><p>TSMC Reports Third Quarter EPS</p>")
    ref = FilingRef(accession="a", form="6-K", filed=None, role="earnings_release", source_url="", exhibits=[exhibit])
    assert classification_excerpt(ref).split("):", 1)[1].strip().startswith("TSMC Reports Third Quarter EPS")


def test_a_per_unit_row_in_a_table_in_millions_is_not_scaled():
    from kpi_extractor.readers.extract import _row_scale
    assert _row_scale("Starlink consumer ARPU ($ per month) $69 $66") == 1.0
    assert _row_scale("Starlink consumer subscribers (# in millions, as of period end) 13.6") == 1e6
    assert _row_scale("Consumer revenues $3,121") is None


def _read_one(html, label, unit, row, col, scale=None, currency="USD"):
    document = parse_document(html, "https://example.com/ex99.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": label, "unit": unit}]}]})
    located = Located.model_validate({"period_end": "2026-09-30", "values": [
        {"kpi": "ops.k", "table": table, "row": row, "col": col, **({"scale": scale} if scale else {})}]})
    values, problems = read_values(document, spec, located, currency)
    assert problems == []
    return values[0]


def test_a_per_unit_kpi_keeps_its_units_in_a_table_in_millions():
    # FedEx's "In thousands, except yield amounts" and ConocoPhillips' "$ Millions, Except as Indicated".
    yield_ = _read_one("""<table><tr><td>In millions, except yield amounts</td><td>Q3 2026</td></tr>
<tr><td>U.S. domestic composite</td><td>$ 15.32</td></tr></table>""", "U.S. Domestic Composite Revenue per Package",
                       "currency", 1, 1, scale=1)
    price = _read_one("""<table><tr><td>($ Millions, Except as Indicated)</td><td>3Q26</td></tr>
<tr><td>Total</td><td>5.70</td></tr></table>""", "Natural Gas Realized Price ($/MCF)", "currency", 1, 1, scale=1)
    revenue = _read_one("""<table><tr><td>($ Millions, Except as Indicated)</td><td>3Q26</td></tr>
<tr><td>Total revenues</td><td>20,057</td></tr></table>""", "Total Revenues", "currency", 1, 1)
    freight = _read_one("""<table><tr><td>In millions, except yield amounts</td><td>Q3 2026</td></tr>
<tr><td>International export composite freight</td><td>$ 1.21</td></tr></table>""", "International Composite Freight Yield",
                        "currency", 1, 1, scale=1)
    assert (yield_.value, price.value, revenue.value, freight.value) == (15.32, 5.70, 20_057_000_000, 1.21)


def test_a_scale_quoted_beside_the_table_beats_the_caption_it_inherits():
    # Goldman 2017: the assets under supervision table follows the VaR table "$ in millions" but is "$ in billions".
    document = parse_document("""<p>Average Daily VaR $ in millions</p><p>Assets Under Supervision $ in billions</p>
<table><tr><td>$ in millions</td><td>Three Months Ended December 31, 2017</td></tr>
<tr><td>Total long-term AUS net inflows / (outflows)</td><td>(1)</td></tr></table>""", "https://example.com/ex99.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": "Long-Term AUS Net Inflows", "unit": "currency"}]}]})
    located = Located.model_validate({"period_end": "2017-12-31", "values": [
        {"kpi": "ops.k", "table": table, "row": 1, "col": 1, "scale": 1e9, "scale_text": "$ in billions"}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == [] and values[0].value == -1_000_000_000
    unquoted = Located.model_validate({"period_end": "2017-12-31", "values": [
        {"kpi": "ops.k", "table": table, "row": 1, "col": 1, "scale": 1e9}]})
    assert read_values(document, spec, unquoted, "USD")[0][0].value == -1_000_000


def test_a_row_label_names_its_currency():
    # Axia: "Average ACL Price (R$/MWh) 193.02" in a table with no declared currency.
    price = _read_one("""<table><tr><td>Generation</td><td>1Q26</td></tr>
<tr><td>Average ACL Price (R$/MWh)</td><td>193.02</td></tr></table>""", "Average ACL Price", "currency", 1, 1, currency=None)
    assert (price.value, price.currency) == (193.02, "BRL")


def test_a_column_spanning_january_to_june_is_not_a_quarter():
    # Philips' sales by country are given only "January to June": never its second quarter.
    from kpi_extractor.readers.extract import column_duration_problem, column_header
    for span, months in (("January to June", 6), ("April to June", 3), ("January–September", 9), ("Oct - Dec", 3)):
        document = parse_document(f"""<table><tr><td>in millions of EUR</td><td>{span}</td><td></td></tr>
<tr><td></td><td>2026</td><td>2025</td></tr><tr><td>United States</td><td>3,281</td><td>3,476</td></tr></table>""",
                                  "https://example.com/ex99.htm")
        header = column_header(next(iter(document.tables.values())), 2, 1)
        assert header.startswith(f"<{months} months>") and "<<" not in header
        assert (column_duration_problem(header, False) is None) == (months == 3)
    assert column_header(parse_document("""<table><tr><td></td><td>Three Months Ended June 30, 2026</td></tr>
<tr><td>Sales</td><td>5</td></tr></table>""", "https://example.com/ex99.htm").tables.values().__iter__().__next__(), 1, 1) \
        == "three months ended @ # #"


def _read_quote(text, label, unit, quote, value_text):
    document = parse_document(f"<p>{text}</p>", "https://example.com/ex99.htm")
    block = next(iter(document.blocks))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": label, "unit": unit}]}]})
    located = Located.model_validate({"period_end": "2026-03-31", "values": [
        {"kpi": "ops.k", "block": block, "quote": quote, "value_text": value_text}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    return values[0].value


def test_a_quoted_fall_makes_a_net_change_count_negative():
    text = "In mobile, our contract customer base declined by 46,000 in the quarter."
    assert _read_quote(text, "UK Mobile Contract Net Additions", "count", "our contract customer base declined by 46,000 "
                       "in the quarter", "46,000") == -46_000
    # A level is never a change: a store count that "declined to 380" stays 380.
    assert _read_quote("Stores declined to 380 at quarter end.", "Stores", "count", "Stores declined to 380 at quarter end",
                       "380") == 380


def test_a_quoted_per_unit_amount_keeps_its_units_under_a_millions_heading():
    text = "($ in millions, except revenue per student amounts) Revenue per student $7,300 $7,400"
    assert _read_quote(text, "Revenue per Student (Concorde)", "currency", "Revenue per student $7,300 $7,400", "$7,300") == 7300
    assert _read_quote("($ in millions) Revenue $7,300", "Revenue", "currency", "Revenue $7,300", "$7,300") == 7_300_000_000


def test_a_footnote_letter_is_no_column_scale_and_mn_is_millions():
    # Axia: "Generation revenue by contracting environment (R$ mn)", a column "Regulatory Revenue (c) = (a) x (b)".
    html = """<p>Table 17 - Generation revenue by contracting environment (R$ mn)</p><table>
<tr><td>Revenue Generation</td><td>Volume (aMW) (a)</td><td>Price (R$/MWh) (b)</td><td>Regulatory Revenue (c) = (a) x (b)</td></tr>
<tr><td></td><td>2Q26</td><td>2Q26</td><td>2Q26</td></tr>
<tr><td>(+) O&amp;M (Quotas)</td><td>1,192</td><td>109</td><td>283</td></tr></table>"""
    quotas = _read_one(html, "O&M (Quotas)", "currency", 2, 3, scale=1e6, currency="BRL")
    assert quotas.value == 283_000_000
    from kpi_extractor.readers.extract import _column_scale
    from kpi_extractor.documents.document import parse_document as parse
    table = next(iter(parse("<table><tr><td></td><td>GDV (Bil.)</td><td>Sales ($B)</td></tr><tr><td>x</td><td>1</td><td>2</td></tr></table>",
                            "https://example.com/x.htm").tables.values()))
    assert _column_scale(table, 1, 1) == 1e9 and _column_scale(table, 1, 2) == 1e9


def test_mm_reads_as_millions_in_a_table_caption():
    # Axia's "Table 4 - Income Statement IFRS (R$ mm)".
    html = """<p>Table 4 - Income Statement IFRS (R$ mm)</p><table><tr><td></td><td>2Q25</td></tr>
<tr><td>Transmission</td><td>5,079</td></tr></table>"""
    assert next(iter(parse_document(html, "https://example.com/x.htm").tables.values())).declared_scale() == 1e6


def test_a_scale_after_a_slash_belongs_to_the_second_measure():
    from kpi_extractor.readers.extract import _row_scale
    assert _row_scale("Leases renewed Kilowatt / NRSF (in thousands) 42,412 92,670") is None
    assert _row_scale("Paid memberships (in millions) 301.6") == 1e6
    renewed = _read_one("""<table><tr><td></td><td>3Q26</td></tr>
<tr><td>Leases renewed Kilowatt / NRSF (in thousands)</td><td>92,670</td></tr></table>""", "Renewal Leasing Volume (kW)", "count", 1, 1)
    assert renewed.value == 92_670


def test_a_metric_named_for_a_place_its_row_never_names_is_held():
    html = """<table><tr><td>INTERNATIONAL BUSINESSES</td><td>3Q25</td></tr>
<tr><td>Policyholder Account Balances at end of period (in millions):</td><td></td></tr>
<tr><td>International Businesses</td><td>53,903</td></tr>
<tr><td>Japan</td><td>537</td></tr></table>"""
    document = parse_document(html, "https://example.com/x.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "japan", "label": "Japan Policyholder Account Balances", "unit": "currency"},
        {"key": "jp", "label": "Japan Life Planners", "unit": "count"}]}]})
    located = Located.model_validate({"period_end": "2025-09-30", "values": [
        {"kpi": "ops.japan", "table": table, "row": 2, "col": 1, "scale": 1e6, "scale_text": "in millions"},
        {"kpi": "ops.jp", "table": table, "row": 3, "col": 1}]})
    values, _problems = read_values(document, spec, located, "USD")
    status = {item.kpi.key: item.status for item in values}
    assert status == {"japan": "needs_review", "jp": "verified"}
    from kpi_extractor.readers.extract import _places
    assert _places("Cross-Border Volume Growth Excluding Intra-Europe", own=True) == set()
    assert _places("8 ASIA PACIFIC") == {"ASIA PACIFIC"} and _places("Asia Pacific Unit Case Volume", own=True) == {"ASIA PACIFIC"}


def test_a_quote_for_the_year_to_date_is_not_the_quarters_count():
    # Borr: "YTD 2025, the company was awarded 14 new contract commitments" is no quarterly award count.
    text = "YTD 2025, the company was awarded 14 new contract commitments, representing 2,584 days."
    document = parse_document(f"<p>{text}</p>", "https://example.com/ex99.htm")
    block = next(iter(document.blocks))
    def read(label):
        spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                                 "kpis": [{"key": "k", "label": label, "unit": "count"}]}]})
        located = Located.model_validate({"period_end": "2025-06-30", "values": [
            {"kpi": "ops.k", "block": block, "quote": "YTD 2025, the company was awarded 14 new contract commitments", "value_text": "14"}]})
        return read_values(document, spec, located, "USD")
    values, problems = read("Contract Commitments Awarded")
    assert values == [] and "not the quarter's" in problems[0]
    values, problems = read("Year-to-Date Contract Commitments")
    assert problems == [] and values[0].value == 14
    # A KPI defined for the year in progress keeps a full-year quote: Borr's current-year contract coverage.
    assert _read_quote("Full year 2025 contract coverage of 85% at an average dayrate of $145,000.", "Current-Year Contract Coverage",
                       "percent", "Full year 2025 contract coverage of 85%", "85%") == 85
    # A quote that names the quarter is the quarter's figure, whatever fiscal year it names (Blue Bird).
    assert _read_quote("In the second quarter of fiscal 2026, 2,148 units booked compared to 2,295.", "Legacy Blue Bird Unit Bookings",
                       "count", "In the second quarter of fiscal 2026, 2,148 units booked", "2,148") == 2148
    # A second-half KPI may quote the first half beside it (Borr's 2026 coverage).
    assert _read_quote("Awards increased 2026 coverage to 80% in the first half and 48% in the second half.", "Second-Half Contract Coverage",
                       "percent", "coverage to 80% in the first half and 48% in the second half", "48%") == 48
    # A year-to-date change that leads to a stock measure's level: the level is the quarter's figure (GE Vernova).
    assert _read_quote("Our Electrification equipment backlog increased $6.5 billion year-to-date, to approximately $26 billion.",
                       "Electrification Equipment Backlog", "currency",
                       "backlog increased $6.5 billion year-to-date, to approximately $26 billion", "$26 billion") == 26_000_000_000
    # A KPI defined over the longer span keeps its quote: cumulative deliveries, trailing flows.
    assert _read_quote("Cumulative deliveries (all-time; mil) 8.5 10.2", "Cumulative Deliveries", "count",
                       "Cumulative deliveries (all-time; mil) 8.5 10.2", "10.2") == 10.2


def test_a_row_that_is_a_year_is_not_a_quarter():
    # Sun Communities' operating statistics by year: the 2025 row is the full year's pre-owned home sales.
    html = """<table><tr><td></td><td>Number of Move-outs</td><td>Pre-owned Home Sales</td></tr>
<tr><td>2025</td><td>10,179</td><td>1,210</td></tr><tr><td>2024</td><td>7,050</td><td>1,554</td></tr></table>"""
    document = parse_document(html, "https://example.com/ex99.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": "Pre-Owned Home Sales", "unit": "count"}]}]})
    quarter = Located.model_validate({"period_end": "2025-12-31", "values": [{"kpi": "ops.k", "table": table, "row": 1, "col": 2}]})
    values, problems = read_values(document, spec, quarter, "USD")
    assert values == [] and "is the year '2025'" in problems[0]
    # A reading that points past the table's last row is a problem for that KPI, never a failed filing.
    beyond = Located.model_validate({"period_end": "2025-12-31", "values": [{"kpi": "ops.k", "table": table, "row": 9, "col": 2}]})
    values, problems = read_values(document, spec, beyond, "USD")
    assert values == [] and problems
    annual = Located.model_validate({"period_end": "2025-12-31", "annual": True, "values": [{"kpi": "ops.k", "table": table, "row": 1, "col": 2}]})
    values, problems = read_values(document, spec, annual, "USD")
    assert problems == [] and values[0].value == 1210


def test_stated_scales_in_more_forms_are_recognised():
    from kpi_extractor.readers.extract import _column_scale, _row_scale
    assert _row_scale("Oncological Plans # (000) 1,005") == 1e3               # Auna's unit column
    assert _row_scale("Paid memberships (Mil.) 301.6") == 1e6
    # FedEx: "Avg. daily package volume (ADV) (000s)(1):" heads the rows under it.
    adv = _read_one("""<table><tr><td>Package statistics</td><td>Q2 2026</td></tr>
<tr><td>Avg. daily package volume (ADV) (000s)(1):</td><td></td></tr>
<tr><td>Total U.S. domestic ADV</td><td>14,716</td></tr></table>""", "U.S. Domestic Average Daily Package Volume", "count", 2, 1)
    assert adv.value == 14_716_000 and adv.status == "verified"
    # Vodafone: a bare "€m" column heading.
    document = parse_document("<table><tr><td></td><td>Q2 FY26</td></tr><tr><td></td><td>€m</td></tr><tr><td>UK</td><td>2,018</td></tr></table>",
                              "https://example.com/x.htm")
    assert _column_scale(next(iter(document.tables.values())), 2, 1) == 1e6
    # Grifols: "In millions of euros" over a breakdown given as text.
    assert _read_quote("In millions of euros Revenue by Business Unit Biopharma 1,653 1,633", "Biopharma", "currency",
                       "Biopharma 1,653 1,633", "1,653") == 1_653_000_000


def test_a_repeat_the_row_itself_prints_is_no_wrong_column():
    from kpi_extractor.quality.checks import _row_shows
    # PNC's card and cash management: 737 in 3Q25 and 2Q25, side by side in the row.
    current = {"locator": {"row_numbers": [737.0, 737.0, 698.0]}}
    assert _row_shows(current, 737_000_000, 737_000_000)
    assert not _row_shows(current, 698_500_000, 737_000_000)
    assert not _row_shows({"locator": {}}, 737_000_000, 737_000_000)


def test_a_section_rows_in_thousands_is_no_column_scale_for_the_volumes_below():
    # MP Materials: "Revenue: (in thousands)" heads the revenue rows; the production volumes below are in metric tons.
    html = """<table><tr><td>For the three months ended September 30,</td><td></td><td></td></tr>
<tr><td>(unaudited)</td><td></td><td>2025</td></tr><tr><td>Revenue:</td><td></td><td>(in thousands)</td></tr>
<tr><td>Rare earth concentrate</td><td>$</td><td>43,053</td></tr><tr><td>Separated NdPr products</td><td></td><td></td></tr>
<tr><td>NdPr Production Volume (MTs)</td><td></td><td>721</td></tr></table>"""
    assert _read_one(html, "NdPr Production Volume (MTs)", "count", 5, 1).value == 721
    # Nor does a heading two rows up: only the nearest heading's scale belongs to the rows under it.
    stacked = """<table><tr><td></td><td>2025</td></tr><tr><td>Revenue: (in thousands)</td><td></td></tr>
<tr><td>Volumes:</td><td></td></tr><tr><td>NdPr Production Volume (MTs)</td><td>721</td></tr></table>"""
    assert _read_one(stacked, "NdPr Production Volume (MTs)", "count", 3, 1).value == 721
    from kpi_extractor.readers.extract import _column_scale
    table = next(iter(parse_document("<table><tr><td></td><td>2Q26 (R$ mn)</td></tr><tr><td>x</td><td>5</td></tr></table>",
                                     "https://example.com/x.htm").tables.values()))
    assert _column_scale(table, 1, 1) == 1e6


def test_a_count_is_never_read_from_a_currency_column():
    # Auna: "2Q'25 (USD) | 2Q'25 | YTD 25" with "Patients treated # (000) 17 54".
    html = """<table><tr><td>Key Operating Metrics</td><td>2Q'25 (USD)</td><td>2Q'25</td></tr>
<tr><td>Patients treated # (000)</td><td>17</td><td>54</td></tr></table>"""
    document = parse_document(html, "https://example.com/x.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": "Patients Treated", "unit": "count"}]}]})
    wrong = Located.model_validate({"period_end": "2025-06-30", "values": [{"kpi": "ops.k", "table": table, "row": 1, "col": 1}]})
    values, problems = read_values(document, spec, wrong, "PEN")
    assert values == [] and "currency's column" in problems[0]
    right = Located.model_validate({"period_end": "2025-06-30", "values": [{"kpi": "ops.k", "table": table, "row": 1, "col": 2}]})
    values, problems = read_values(document, spec, right, "PEN")
    assert problems == [] and values[0].value == 54_000


def test_a_row_label_with_a_short_scale_scales_its_count():
    # Tesla: "Active FSD Subscriptions (mil) 0.8 0.8 0.9 1.0 1.1"; a footnote "(b)" is never billions.
    fsd = _read_one("""<table><tr><td>($ in millions)</td><td>Q4-2025</td></tr>
<tr><td>Active FSD Subscriptions (mil)</td><td>1.1</td></tr></table>""", "Active FSD Subscriptions", "count", 1, 1)
    assert round(fsd.value) == 1_100_000
    from kpi_extractor.readers.extract import _row_scale
    assert _row_scale("Stores (b) 381") is None


def test_a_table_in_two_currencies_takes_each_columns_own():
    # Auna: "(Figures in millions of Soles and millions of US Dollars)" with a "2Q'25 (USD)" column beside "2Q'25".
    html = """<table><tr><td>(Figures in millions of Soles and millions of US Dollars)</td><td>2Q'25 (USD)</td><td>2Q'25</td></tr>
<tr><td>Holding and eliminations</td><td>(23)</td><td>(81)</td></tr>
<tr><td>Average monthly revenue per plan member</td><td>18.02</td><td>S/ 62.90</td></tr></table>"""
    soles = _read_one(html, "Holding and Eliminations", "currency", 1, 2, currency="PEN")
    dollars = _read_one(html, "Holding and Eliminations", "currency", 1, 1, currency="PEN")
    per_member = _read_one(html, "Average Monthly Revenue per Plan Member", "currency", 2, 2, currency="USD")
    assert (soles.value, soles.currency) == (-81_000_000, "PEN")
    assert (dollars.value, dollars.currency) == (-23_000_000, "USD")
    assert (per_member.value, per_member.currency) == (62.90, "PEN")


def test_a_count_spelled_out_is_read():
    from kpi_extractor.documents.document import parse_number, word_number
    assert word_number("nine countries").value == 9 and word_number("twenty-five").value == 25
    assert word_number("two billion").value == 2e9
    assert parse_number("Three Months Ended") is None  # tables keep reading digits only


def test_a_quote_takes_the_last_scale_heading_before_it_in_its_block():
    # Apollo's highlights page: earnings "($ in millions …)", then AUM and business drivers "($ in billions)".
    from kpi_extractor.ai import KpiSpec, Locator
    from kpi_extractor.readers.extract import _read_raw
    document = parse_document(
        "<p>($ in millions, except per share amounts) Fee Related Earnings $785 Assets Under Management ($ in billions) "
        "Total Assets Under Management $1,047 Fee-Generating AUM $858 Business Drivers ($ in billions) Inflows $60</p>",
        "https://www.sec.gov/x.htm")
    block = document.order[0]
    def read(quote, value_text):
        return _read_raw(document, Locator(kpi="operating.x", block=block, quote=quote, value_text=value_text),
                         KpiSpec(key="x", label="x", unit="currency"), "USD")[0]
    assert read("Fee-Generating AUM $858", "$858") == 858e9
    assert read("Inflows $60", "$60") == 60e9
    assert read("Fee Related Earnings $785", "$785") == 785e6


def _metric_spec(*kpis):
    return Spec.model_validate({"groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric",
                                             "kpis": [{"key": key, "label": label, "unit": unit} for key, label, unit in kpis]}]})


def test_a_quote_that_calls_a_change_a_fall_is_read_as_negative():
    # Meta 2023: "the average price per ad decreased by 16%" was served as +16. Visa and Meta's headcount the same way.
    document = parse_document("""
<p>Ad impressions increased by 34% year-over-year and the average price per ad decreased by 16% year-over-year.</p>
<p>Headcount was 75,472 as of June 30, 2026, a decrease of 1% year-over-year.</p>
<p>Cross-border volume fell, and the decline on a constant- dollar basis was 5% in the quarter.</p>
<p>Unit volume was down 3% while price/mix was 7%.</p>
<p>Gross margin declined to 30%, and costs decreased by 3% and margin was 40%.</p>
""", "https://example.com/ex99.htm")
    spec = _metric_spec(("impressions", "Ad Impressions Year over Year Growth", "percent"),
                        ("price", "Average Price per Ad Year over Year Growth", "percent"),
                        ("headcount", "Headcount Year over Year Growth", "percent"),
                        ("cross_border", "Cross-Border Volume Growth", "percent"),
                        ("units", "Unit Volume Growth", "percent"), ("mix", "Price Mix", "percent"),
                        ("margin", "Gross Margin", "percent"), ("op_margin", "Margin", "percent"))
    quotes = [("impressions", "Ad impressions increased by 34%", "34%"), ("price", "the average price per ad decreased by 16%", "16%"),
              ("headcount", "a decrease of 1% year-over-year", "1%"), ("cross_border", "the decline on a constant- dollar basis was 5%", "5%"),
              ("units", "Unit volume was down 3%", "3%"), ("mix", "while price/mix was 7%", "7%"),
              ("margin", "Gross margin declined to 30%", "30%"), ("op_margin", "costs decreased by 3% and margin was 40%", "40%")]
    located = Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": f"operating.{key}", "block": _block(document, quote), "quote": quote, "value_text": value} for key, quote, value in quotes]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    assert {item.kpi.key: item.value for item in values} == {"impressions": 34, "price": -16, "headcount": -1, "cross_border": -5,
                                                             "units": -3, "mix": 7, "margin": 30, "op_margin": 40}


def test_a_net_figure_never_fills_a_gross_kpi():
    # KKR 2019-2020: "Net Unrealized Carried Interest" was served as Gross Unrealized Carried Interest.
    document = parse_document("""
<table>
<tr><td>($ in thousands)</td><td>June 30, 2020</td></tr>
<tr><td>Net Unrealized Carried Interest</td><td>1,305,013</td></tr>
<tr><td>Gross Unrealized Carried Interest</td><td>2,610,026</td></tr>
</table>
<p>Net unrealized carried interest was $1.3 billion as of June 30, 2020.</p>
""", "https://example.com/ex99.htm")
    spec = _metric_spec(("gross_carry", "Gross Unrealized Carried Interest", "currency"))
    table = next(iter(document.tables))
    for wrong in ({"kpi": "operating.gross_carry", "table": table, "row": 1, "col": 1, "scale": 1000},
                  {"kpi": "operating.gross_carry", "block": _block(document, "Net unrealized"),
                   "quote": "Net unrealized carried interest was $1.3 billion", "value_text": "$1.3 billion"}):
        values, problems = read_values(document, spec, Located.model_validate({"period_end": "2020-06-30", "values": [wrong]}), "USD")
        assert values == [] and "net figure" in problems[0]
    right = {"kpi": "operating.gross_carry", "table": table, "row": 2, "col": 1, "scale": 1000}
    values, problems = read_values(document, spec, Located.model_validate({"period_end": "2020-06-30", "values": [right]}), "USD")
    assert problems == [] and [item.value for item in values] == [2_610_026_000]


def test_a_release_holding_a_reading_reader_3_rejects_is_read_again_once():
    from types import SimpleNamespace

    from kpi_extractor.steps.release_flow import ReleaseFlow, stored_reading_fails

    fall = {"method": "ai", "validation_status": "verified", "unit": "percent", "value": 16, "source_accession": "A1",
            "kpi_label": "Average Price per Ad Year over Year Growth",
            "locator": {"quote": "the average price per ad decreased by 16% year-over-year", "value_text": "16%"}}
    net = {"method": "ai", "validation_status": "verified", "unit": "currency", "value": 1.3e9, "source_accession": "A2",
           "kpi_label": "Gross Unrealized Carried Interest", "locator": {"row_label": "Net Unrealized Carried Interest"}}
    assert stored_reading_fails(fall) and stored_reading_fails(net)
    assert not stored_reading_fails({**fall, "value": -16})
    assert not stored_reading_fails({**fall, "owner_decision": "confirmed"})
    assert not stored_reading_fails({**net, "locator": {"row_label": "Gross Unrealized Carried Interest"}})
    flow = SimpleNamespace(values={"a": fall, "b": net})
    assert ReleaseFlow._reader3_due(flow, {"notes": ["read by AI", "release reader 2"]}, "A1")
    assert ReleaseFlow._reader3_due(flow, {"notes": ["read by AI"]}, "A2")
    assert not ReleaseFlow._reader3_due(flow, {"notes": ["read by AI", "release reader 3"]}, "A1")
    assert not ReleaseFlow._reader3_due(flow, {"notes": ["read by AI", "release reader 2"]}, "A9")


def test_week_based_columns_are_told_from_their_half_years():
    from kpi_extractor.readers.extract import column_duration_problem

    assert column_duration_problem("<12 weeks> ended @ #", annual=False) is None
    assert column_duration_problem("<16 weeks> ended @ #", annual=False) is None
    assert column_duration_problem("sixteen weeks ended @ #", annual=False) is None
    assert column_duration_problem("<24 weeks> ended @ #", annual=False) is not None
    assert column_duration_problem("<36 weeks> ended @ #", annual=False) is not None
    assert column_duration_problem("<28 weeks> ended @ #", annual=False) is not None


def test_unlisted_rows_of_a_complete_table_are_placed_or_left_out():
    # JPMorgan's revenue table: fee lines, their subtotal, then interest income and expense, which only make up net
    # interest income. The owner's list names the fee lines (under Noninterest Revenue), Net Interest Income and the total.
    document = parse_document("""
<table>
<tr><td>(in millions)</td><td>1Q17</td></tr>
<tr><td>Investment banking fees</td><td>1,817</td></tr>
<tr><td>Securities gains</td><td>(3)</td></tr>
<tr><td>Card income</td><td>914</td></tr>
<tr><td>Total noninterest revenue</td><td>2,728</td></tr>
<tr><td>Interest income</td><td>15,042</td></tr>
<tr><td>Interest expense</td><td>2,978</td></tr>
<tr><td>Net interest income</td><td>12,064</td></tr>
<tr><td>Total net revenue</td><td>14,792</td></tr>
</table>""", "https://example.com/supplement.htm")
    spec = Spec.model_validate({"groups": [{"key": "product", "label": "Revenue by Product", "kind": "revenue_breakdown", "total_kpi": "total",
        "kpis": [{"key": "ib", "label": "Investment Banking Fees", "unit": "currency", "parent": "nonint"},
                 {"key": "card", "label": "Card Income", "unit": "currency", "parent": "nonint"},
                 {"key": "nonint", "label": "Total Noninterest Revenue", "unit": "currency"},
                 {"key": "nii", "label": "Net Interest Income", "unit": "currency"},
                 {"key": "total", "label": "Total Net Revenue", "unit": "currency"}]}]})
    located = Located.model_validate({"period_end": "2017-03-31", "tables": [
        {"group": "product", "table": "T0", "col": 1, "first_row": 1, "last_row": 7, "total_row": 8, "scale": 1000000}]})
    values, problems = read_values(document, spec, located, "USD")
    assert problems == []
    read = {item.kpi.key: (item.value, item.kpi.parent) for item in values if not item.is_total}
    assert "interestincome" not in read and "interestexpense" not in read
    assert read["securitiesgains"] == (-3_000_000, "nonint")
    validate_groups(values, previous={})
    assert all(item.status == "verified" for item in values)


def test_a_forecast_is_never_read_as_the_quarter():
    # Broadcom, Q3 FY2024: "We expect revenue from AI to be $12 billion for fiscal year 2024" was read as the quarter.
    document = parse_document("""
<p>We expect revenue from AI to be $12 billion for fiscal year 2024 driven by Ethernet networking.</p>
<p>Q1 AI revenue of $8.4 billion grew 106% year-over-year, above our forecast.</p>
<p>Current remaining performance obligation, which represents the future revenues under contract expected to be recognized over the next 12 months, was $11.9 billion.</p>
""", "https://example.com/ex99.htm")
    spec = _metric_spec(("ai", "AI Semiconductor Revenue", "currency"), ("crpo", "Current Remaining Performance Obligation", "currency"))
    def read(kpi, quote, value_text):
        located = Located.model_validate({"period_end": "2024-08-04", "values": [
            {"kpi": f"operating.{kpi}", "block": _block(document, quote[:20]), "quote": quote, "value_text": value_text}]})
        return read_values(document, spec, located, "USD")
    values, problems = read("ai", "We expect revenue from AI to be $12 billion", "$12 billion")
    assert values == [] and "forecast" in problems[0]
    assert [item.value for item in read("ai", "Q1 AI revenue of $8.4 billion grew 106% year-over-year, above our forecast", "$8.4 billion")[0]] == [8.4e9]
    assert [item.value for item in read("crpo", "Current remaining performance obligation, which represents the future revenues under contract expected to be recognized over the next 12 months, was $11.9 billion", "$11.9 billion")[0]] == [11.9e9]


def test_an_explanation_must_be_about_the_figure_and_the_quarter():
    from kpi_extractor.readers.extract import explanation_supports

    assert explanation_supports("Unit case volume declined 36% driven by India and South Africa due to the coronavirus.", "Bottling Investments Unit Case Volume Growth", -36)
    assert explanation_supports("The increase in Incentive fees was driven by fund distributions.", "Incentive fees", 587e6)
    assert explanation_supports("Inflows from Asset Management of $95 billion in the first quarter were driven by Athora's acquisition", "Asset Management Inflows", 95e9)
    # Guidance, another period, another line, or a sentence that names nothing of the KPI: not an explanation.
    assert not explanation_supports("We expect revenue from AI to be $12 billion for fiscal year 2024 driven by Ethernet networking", "AI Semiconductor Revenue", 12e9)
    assert not explanation_supports("resulting in approximately $6.8 billion of inflows attributable to Athene for the first six months of 2023", "Inflows Attributable to Athene", 3.101e9)
    assert not explanation_supports("Pension Group Annuities: Closed one transaction during the quarter despite heightened competition", "Other Spread Products Inflows", 577e6)
    assert not explanation_supports("Second vintage of strategic third-party sidecar is expected to begin supporting Athene's organic growth", "Inflows Attributable to ADIP", 31e6)
    assert not explanation_supports("AUM were impacted by market volatility and outflows in the current period.", "Long-term Net Flows", -14.4e9)


def test_an_unsigned_figure_listed_after_a_fall_is_held():
    # Vodafone UK: "(Q3: 73,000, Q4: 22,000)" after "declined by" were two falls; Germany TV's "declined by 6,000
    # (Q2: 62,000)" listed a gain. Neither sign can be trusted, so the figure is held, not accepted.
    text = "Our mobile contract customer base declined by 127,000 during the year (Q3: 73,000, Q4: 22,000) including losses."
    document = parse_document(f"<p>{text}</p>", "https://example.com/ex99.htm")
    block = next(iter(document.blocks))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "k", "label": "UK Mobile Contract Net Additions", "unit": "count"}]}]})
    quote = "Our mobile contract customer base declined by 127,000 during the year (Q3: 73,000, Q4: 22,000)"
    located = Located.model_validate({"period_end": "2026-03-31", "values": [
        {"kpi": "ops.k", "block": block, "quote": quote, "value_text": "22,000"}]})
    values, _ = read_values(document, spec, located, "USD")
    assert values[0].status == "needs_review"
    # A list that gives its signs is read as it stands, and a fall stated next to its figure still makes it negative.
    signed = "contract customer base declined by 103,000 during the year (Q3: 11,000, Q4: -77,000)"
    assert _read_quote(signed, "Germany Mobile Contract Net Additions", "count", signed, "-77,000") == -77_000
    assert not listed_after_fall(signed, "-77,000")
    assert not listed_after_fall("Our broadband customer base increased by 222,000 in FY26 (Q3: 64,000, Q4: 64,000)", "64,000")
    assert _read_quote("In mobile, our contract customer base declined by 46,000 in the quarter.", "UK Mobile Contract Net "
                       "Additions", "count", "our contract customer base declined by 46,000 in the quarter", "46,000") == -46_000


def test_a_footnote_mark_after_a_place_still_names_it():
    from kpi_extractor.readers.extract import _places
    assert _places("Other Europe1") == {"EUROPE"}
    assert _places("Europeans") == set()


def test_a_dayrate_keeps_its_units_under_a_millions_heading():
    # Borr: "(in millions)" heads its release; "an average dayrate of approximately $137,000" is $137,000 a day.
    text = "(In US$ millions) Revenue 250. We have contracts at an average dayrate of approximately $137,000."
    assert _read_quote(text, "Average Dayrate", "currency", "average dayrate of approximately $137,000", "$137,000") == 137_000
    assert _read_quote("(In US$ millions) Dayrate revenue 241.0", "Dayrate revenue", "currency", "Dayrate revenue 241.0",
                       "241.0") == 241_000_000


def test_a_quarter_label_keeps_its_meaning_in_a_column_header():
    from kpi_extractor.readers.extract import column_duration_problem, column_header
    from kpi_extractor.documents.document import Table
    table = Table(id="T1", rows=[["", "Q4", "Q3", "Fiscal 2025"], ["PCL ratio", "0.58", "0.55", "0.62"]], context="")
    # Scotiabank's fourth-quarter ratio is no fiscal year's figure, and a year-to-date column is no quarter's.
    assert column_duration_problem(column_header(table, 1, 1), annual=True)
    assert column_duration_problem(column_header(table, 1, 3), annual=True) is None
    assert column_header(table, 1, 1) == "<fourth quarter>" and column_header(table, 1, 2) == "<third quarter>"
    ytd = Table(id="T2", rows=[["", "3Q26", "YTD", "LTM"], ["Rent change", "5.9", "5.1", "4.9"]], context="")
    assert column_duration_problem(column_header(ytd, 1, 1), annual=False) is None
    assert column_duration_problem(column_header(ytd, 1, 2), annual=False)
    assert column_duration_problem(column_header(ytd, 1, 3), annual=False)


def test_a_level_at_the_period_end_may_come_from_a_year_to_date_column():
    from kpi_extractor.readers.extract import _stands_at_period_end
    from kpi_extractor.ai import KpiSpec
    assert _stands_at_period_end(KpiSpec(key="b", label="Total Bed Capacity", unit="count"))
    assert not _stands_at_period_end(KpiSpec(key="m", label="Oncology Plan Medical Loss Ratio", unit="percent"))
    assert not _stands_at_period_end(KpiSpec(key="r", label="Average Monthly Revenue per Plan Member", unit="currency"))
    assert not _stands_at_period_end(KpiSpec(key="c", label="Contract Commitments Awarded", unit="count"))


def test_a_sub_row_named_twice_under_two_parents_is_held():
    from kpi_extractor.readers.extract import _row_named_twice
    from kpi_extractor.documents.document import Table
    rows = [["", "2Q'26"], ["Healthcare Services Mexico", "300"], ["Healthcare Services Colombia", "426"],
            ["- Healthcare Services Colombia", "427"], ["- Holding and eliminations", "(1)"],
            ["Healthcare Services Peru & Oncosalud Peru", "512"], ["- Healthcare Services Peru", "296"],
            ["- Oncosalud Peru", "317"], ["- Holding and eliminations", "(101)"], ["Total Revenue", "1,238"],
            ["Adjusted EBITDA", ""], ["Healthcare Services Mexico", "75"]]
    table = Table(id="T34", rows=rows, context="")
    assert _row_named_twice(table, 8, 1)  # Auna's Peru eliminations are not the group's
    assert _row_named_twice(table, 1, 1) is None  # a segment named again in the EBITDA section is not a twin
    assert _row_named_twice(table, 6, 1) is None


def test_a_reading_taken_for_the_year_keeps_only_the_year_and_its_end_levels():
    from types import SimpleNamespace
    from kpi_extractor.ai import KpiSpec
    from kpi_extractor.steps.release_flow import _year_columns_only
    item = lambda label, unit, header: SimpleNamespace(kpi=KpiSpec(key=label[:3].lower(), label=label, unit=unit),
                                                     group=SimpleNamespace(key="ops"), locator={"header": header})
    ratio = item("Provision for Credit Losses Ratio", "percent", "<fourth quarter>")
    deposits = item("Deposits", "currency", "<fourth quarter>")
    third = item("Deposits", "currency", "<third quarter>")
    year = item("Productivity Ratio", "percent", "for the year ended @ #")
    kept, dropped = _year_columns_only([ratio, deposits, third, year])
    assert kept == [deposits, year] and len(dropped) == 2


def test_a_slide_title_after_its_figures_names_their_period():
    import pytest
    text = ("5 Fleet Overview 22 New Commitments $625M Added Backlog Revenue $130k Avg Dayrate 4,820+ Backlog Days "
            "2025 YTD Contracting Statistics Delivering strong coverage")
    with pytest.raises(AssertionError):
        _read_quote(text, "Contract Commitments Awarded", "count", "22 New Commitments $625M Added Backlog Revenue", "22")
    # A KPI defined over the year to date reads it, and a level at the quarter's end is the quarter's.
    assert _read_quote(text, "Contract Commitments Year to Date", "count", "22 New Commitments $625M Added Backlog Revenue", "22") == 22
    assert _read_quote("Ending backlog $1.2 billion. YTD orders rose.", "Ending Backlog", "currency", "Ending backlog $1.2 billion",
                       "$1.2 billion") == 1_200_000_000


def test_a_table_reading_keeps_the_releases_own_figure_for_the_year_before():
    from datetime import date
    from kpi_extractor.documents.document import Table
    from kpi_extractor.rules.period import year_ago_value
    # "Three Months Ended September 30," over 2026 and 2025, then the six months: the year-ago quarter, scaled as read.
    dated = Table(id="T1", context="", rows=[
        ["", "Three Months Ended September 30,", "Three Months Ended September 30,", "Nine Months Ended September 30,", "Nine Months Ended September 30,"],
        ["", "2026", "2025", "2026", "2025"],
        ["Cloud revenue", "1,234", "1,100", "3,600", "3,100"]])
    assert year_ago_value(dated, 2, 1, date(2026, 9, 30), 1_234_000_000) == 1_100_000_000
    # Quarter labels: 3Q26 beside 2Q26 and 3Q25; a change column is no comparative.
    labelled = Table(id="T2", context="", rows=[["", "3Q26", "2Q26", "3Q25", "% change"], ["Gross margin", "35.7%", "34.9%", "36.1%", "(0.4)%"]])
    assert year_ago_value(labelled, 1, 1, date(2026, 9, 30), 35.7) == 36.1
    # Two columns a year back (two segments side by side, both headed 2025): no one comparative to vouch for.
    twice = Table(id="T3", context="", rows=[["", "2026", "2025", "2026", "2025"], ["Units", "10", "9", "20", "18"]])
    assert year_ago_value(twice, 1, 1, date(2026, 12, 31), 10) is None
    assert year_ago_value(dated, 2, 1, None, 1_234_000_000) is None


def test_one_place_written_two_ways_is_named():
    # Coca-Cola's "EMEA Unit Case Volume Growth" is read from its "Europe, Middle East & Africa" row.
    from kpi_extractor.readers.extract import _places
    from kpi_extractor.rules.identity import _place_not_named
    html = """<table><tr><td></td><td>Unit Case Volume</td></tr>
<tr><td>Europe, Middle East &amp; Africa</td><td>5%</td></tr><tr><td>Latin America</td><td>2%</td></tr>
<tr><td>U.S.</td><td>1%</td></tr></table>"""
    document = parse_document(html, "https://example.com/x.htm")
    table = document.tables[next(iter(document.tables))]
    assert _place_not_named("EMEA Unit Case Volume Growth", table, 1, 1) is None
    assert _place_not_named("United States Unit Case Volume Growth", table, 3, 1) is None
    assert _place_not_named("APAC Unit Case Volume Growth", table, 2, 1) == "ASIA PACIFIC"
    # A row that says only EMEA never names Europe alone; a figure named for all three is EMEA's.
    assert _places("Europe Revenue", own=True) - _places("EMEA") == {"EUROPE"}
    assert _places("Europe, Middle East and Africa Revenue", own=True) == {"EMEA"}
    assert _places("EMEA") <= _places("Europe, Middle East & Africa")


KO_Q4 = """<p>Revenues and Volume</p><table>
<tr><td>Percent Change</td><td>Concentrate Sales1</td><td>Price/Mix</td><td>Unit Case Volume3</td></tr>
<tr><td>Consolidated</td><td>5</td><td>9</td><td>2</td></tr>
<tr><td>Europe, Middle East &amp; Africa</td><td>6</td><td>11</td><td>0</td></tr>
<tr><td>Latin America</td><td>3</td><td>23</td><td>2</td></tr></table>
<p>Revenues and Volume</p><table>
<tr><td>Percent Change</td><td>Concentrate Sales1</td><td>Price/Mix</td><td>Unit Case Volume</td></tr>
<tr><td>Consolidated</td><td>2</td><td>11</td><td>1</td></tr>
<tr><td>Europe, Middle East &amp; Africa</td><td>(1)</td><td>17</td><td>0</td></tr>
<tr><td>Latin America</td><td>3</td><td>21</td><td>3</td></tr></table>"""


def test_the_later_of_two_tables_alike_is_not_the_quarter():
    # Coca-Cola's Q4 2024 release: the quarter's volume table, then the full year's with the same columns and rows and
    # nothing naming either period. The re-read of 2026-11-10 took the year's 1% and 3% for the quarter's 2% and 2%.
    document = parse_document(KO_Q4, "https://example.com/ko.htm")
    first, second = list(document.tables)
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "ucv", "label": "Unit Case Volume Growth", "unit": "percent"},
        {"key": "latam", "label": "Latin America Unit Case Volume Growth", "unit": "percent"}]}]})
    def read(table):
        located = Located.model_validate({"period_end": "2024-12-31", "values": [
            {"kpi": "ops.ucv", "table": table, "row": 1, "col": 3, "row_label": "Consolidated"},
            {"kpi": "ops.latam", "table": table, "row": 3, "col": 3, "row_label": "Latin America"}]})
        return read_values(document, spec, located, "USD")
    values, problems = read(second)
    assert values == [] and len(problems) == 2 and f"the quarter's is {first}" in problems[0]
    values, problems = read(first)
    assert {item.kpi.key: item.value for item in values} == {"ucv": 2, "latam": 2} and problems == []


def test_a_table_repeated_whole_or_with_its_own_period_is_no_twin():
    from kpi_extractor.rules.table import earlier_twin
    same = parse_document(KO_Q4.split("<p>Revenues and Volume</p>")[1] * 2, "https://example.com/x.htm")
    assert earlier_twin(same, list(same.tables)[1]) is None  # the same figures again: either reads the same
    dated = parse_document(KO_Q4.replace("<td>Percent Change</td><td>Concentrate Sales1</td><td>Price/Mix</td><td>Unit Case Volume</td>",
                                         "<td>Year Ended</td><td>Concentrate Sales1</td><td>Price/Mix</td><td>Unit Case Volume</td>"),
                           "https://example.com/y.htm")
    assert earlier_twin(dated, list(dated.tables)[1]) is None


def test_a_quote_naming_only_the_quarter_is_not_a_twelve_month_kpis_figure():
    # KKR's 2016 Q4 release: the year's return is the LTM return at a fourth quarter; the quarter's 3.4% is not.
    text = ("For the year ended December 31, 2016, KKR's private equity portfolio appreciated 11.9%. "
            "For the quarter ended December 31, 2016, KKR's private equity portfolio appreciated 3.4%.")
    label = "Traditional Private Equity Portfolio (LTM Gross Return)"
    assert _read_quote(text, label, "percent", "For the year ended December 31, 2016, KKR's private equity portfolio "
                                               "appreciated 11.9%", "11.9%") == 11.9
    document = parse_document(f"<p>{text}</p>", "https://example.com/ex99.htm")
    block = next(iter(document.blocks))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "k", "label": label, "unit": "percent"}]}]})
    located = Located.model_validate({"period_end": "2016-12-31", "values": [{"kpi": "ops.k", "block": block, "value_text": "3.4%",
        "quote": text}]})
    values, problems = read_values(document, spec, located, "USD")
    assert values == [] and "the quarter's figure" in problems[0]
    # A sentence giving both spans keeps its twelve-month figure: "For the quarter and last twelve months ended June 30".
    both = "For the quarter and last twelve months ended June 30, 2016, KKR's private equity portfolio appreciated 4.5% and 6.0%, respectively."
    assert _read_quote(both, label, "percent", both, "6.0%") == 6.0
    # A quarter's own KPI is untouched.
    assert _read_quote(text, "Private Equity Portfolio Quarterly Return", "percent",
                       "For the quarter ended December 31, 2016, KKR's private equity portfolio appreciated 3.4%", "3.4%") == 3.4


META_Q4_2023 = """
<ul><li>Ad impressions and price per ad &#8211; In the fourth quarter of 2023, ad impressions delivered across our Family of
Apps increased by 21% year-over-year and the average price per ad increased by 2% year-over-year. For the full year 2023,
ad impressions increased by 28% year-over-year and the average price per ad decreased by 9% year-over-year.</li></ul>
"""


def test_a_quote_whose_sentence_opens_with_another_period_is_not_the_quarters():
    # Meta Q4 2023: a quote of the full-year sentence's second half read the year's -9% as the quarter's +2%.
    document = parse_document(META_Q4_2023, "https://example.com/ex99.htm")
    spec = _metric_spec(("price", "Average Price per Ad Year over Year Growth", "percent"))
    def read(quote, value):
        located = Located.model_validate({"period_end": "2023-12-31", "values": [
            {"kpi": "operating.price", "block": _block(document, quote), "quote": quote, "value_text": value}]})
        return read_values(document, spec, located, "USD")
    values, problems = read("the average price per ad decreased by 9% year-over-year", "9%")
    assert values == [] and "'for the full year', not the quarter's" in problems[0]
    values, problems = read("the average price per ad increased by 2% year-over-year", "2%")
    assert problems == [] and [item.value for item in values] == [2]


def test_a_percent_is_never_read_from_an_amount_under_another_columns_percent_heading():
    # Wells Fargo's Q3 2022 supplement: every table heads a "% Change from" column; Consumer Banking's "Total loans"
    # ($335,644 million) was taken for allowance coverage, which is "Total loans" under "... as a percentage of:".
    head = "<tr><td></td><td>Quarter ended</td><td></td><td>Sep 30, 2022 % Change from</td></tr>" \
           "<tr><td>($ in millions)</td><td>Sep 30,2022</td><td>Jun 30,2022</td><td>Jun 30,2022</td></tr>"
    html = (f"<table>{head}<tr><td>Auto</td><td>55,430</td><td>56,813</td><td>(2)</td></tr>"
            "<tr><td>Total loans</td><td>$335,644</td><td>330,859</td><td>1</td></tr></table>"
            f"<table>{head}<tr><td>Allowance for credit losses for loans</td><td>$13,225</td><td>12,884</td><td>3</td></tr>"
            "<tr><td>Allowance for credit losses for loans as a percentage of:</td><td></td><td></td><td></td></tr>"
            "<tr><td>Total loans</td><td>1.40</td><td>1.37</td><td></td></tr></table>")
    document = parse_document(html, "https://example.com/wfc.htm")
    amounts, ratios = list(document.tables)
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating", "kind": "metric", "kpis": [
        {"key": "coverage", "label": "Allowance coverage", "unit": "percent"}]}]})
    def read(table, row):
        return read_values(document, spec, Located.model_validate({"period_end": "2022-09-30", "values": [
            {"kpi": "ops.coverage", "table": table, "row": row, "col": 1, "row_label": "Total loans"}]}), "USD")
    values, problems = read(amounts, 3)
    assert values == [] and "is an amount, not a percentage" in problems[0]
    values, problems = read(ratios, 4)
    assert [item.value for item in values] == [1.40] and problems == []
