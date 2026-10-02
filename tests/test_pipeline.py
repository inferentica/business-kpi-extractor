import json
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.document import parse_document
from kpi_extractor.evaluate import score
from kpi_extractor.extract import read_values
from kpi_extractor.pipeline import SymbolPipeline, _template, apply_maintenance
from kpi_extractor.xbrl import XbrlCandidate

RELEASE = """
<p>In the quarter, 3-nanometer accounted for 30% of wafer revenue; 5-nanometer accounted for 33%.</p>
<p>A year ago, 3-nanometer accounted for 20% of wafer revenue.</p>
<p>Other nodes: 37%.</p>
"""

SPEC = Spec.model_validate({"groups": [{"key": "technology", "label": "Revenue by Technology", "kind": "mix", "kpis": [
    {"key": "n3", "label": "3nm", "unit": "percent"}, {"key": "n5", "label": "5nm", "unit": "percent"},
    {"key": "other", "label": "Other", "unit": "percent"},
]}]})
EXPECTED = date(2026, 6, 30)


def _quote(document, kpi, needle, value):
    block = next(key for key, item in document.blocks.items() if needle in item.text)
    return {"kpi": kpi, "block": block, "quote": needle, "value_text": value}


def _answer(document, n3="3-nanometer accounted for 30%", n3_value="30%"):
    return {"period_end": "2026-06-30", "values": [
        _quote(document, "technology.n3", n3, n3_value),
        _quote(document, "technology.n5", "5-nanometer accounted for 33%", "33%"),
        _quote(document, "technology.other", "Other nodes: 37%", "37%"),
    ]}


class FakeControl:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def ai(self, symbol, purpose, system, user, thinking, model="flash"):
        self.calls.append((purpose, model))
        return json.dumps(self.answers[(purpose, model)])

    def call(self, operation, **payload):
        self.calls.append((operation, None))
        return {}


def _pipeline(control, values=()):
    pipeline = SymbolPipeline(control, "TSM", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="TSMC", fiscal_year_end="1231", foreign=True)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    return pipeline


def _history(document, answer, period_end="2026-03-31", fiscal_period="Q1"):
    """Last quarter's stored values, found in the same places as this quarter's answer (with different numbers)."""
    items, _ = read_values(document, SPEC, ai.Located.model_validate(answer), None)
    return [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": fiscal_period,
             "period_end": period_end, "method": "ai", "validation_status": "verified", "value": item.value - 1,
             "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
             "source_url": "https://www.sec.gov/x.htm"} for item in items]


def test_first_quarters_are_read_by_both_models():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    control = FakeControl({("locate", "flash"): _answer(document), ("locate", "pro"): _answer(document)})
    read, _problems, _located = _pipeline(control)._read("prompt", document, SPEC, EXPECTED)
    assert {item.kpi.key: item.value for item in read} == {"n3": 30, "n5": 33, "other": 37}
    assert ("locate", "pro") in control.calls and ("review", "pro") not in control.calls


def test_flash_alone_when_everything_is_where_it_was_last_quarter():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    control = FakeControl({("locate", "flash"): _answer(document)})
    pipeline = _pipeline(control, _history(document, _answer(document)))
    read, _, _ = pipeline._read("prompt", document, SPEC, EXPECTED)
    assert len(read) == 3
    assert control.calls == [("locate", "flash")]
    assert pipeline.result.flash_only_reads == 1


def test_a_reading_in_a_new_place_brings_in_pro_and_a_review():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    control = FakeControl({
        ("locate", "flash"): _answer(document, "3-nanometer accounted for 20%", "20%"),
        ("locate", "pro"): _answer(document),
        ("review", "pro"): {"choices": {"technology.n3": "B"}},
    })
    history = _history(document, _answer(document))
    history[0]["locator"] = {"block": "P9", "quote": "Leading-edge 3-nanometer share: 29%", "value_text": "29%"}
    read, _, _ = _pipeline(control, history)._read("prompt", document, SPEC, EXPECTED)
    values = {item.kpi.key: item for item in read}
    assert values["n3"].value == 30 and "confirmed by review" in values["n3"].notes
    assert ("review", "pro") in control.calls


def test_review_rejecting_both_readings_drops_the_kpi():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    flash = {"period_end": "2026-06-30", "values": [_quote(document, "technology.n3", "3-nanometer accounted for 20%", "20%")]}
    control = FakeControl({
        ("locate", "flash"): flash,
        ("locate", "pro"): {"period_end": "2026-06-30", "values": []},
        ("review", "pro"): {"choices": {"technology.n3": "none"}},
    })
    read, problems, _ = _pipeline(control)._read("prompt", document, SPEC, EXPECTED)
    assert read == []
    assert any("kept neither" in problem for problem in problems)


def test_quote_templates_ignore_numbers_and_period_words():
    assert _template("DAP was 3.60 billion on average for June 2026") == _template("DAP was 3.54 billion on average for March 2026")
    assert _template("DAP was 3.60 billion") != _template("MAP was 3.60 billion")


def test_audit_adds_kpis_and_retires_only_what_exists():
    updated = apply_maintenance(SPEC, ai.Maintenance.model_validate({
        "add_kpis": {"technology": [{"key": "n2", "label": "2nm", "unit": "percent"},
                                    {"key": "n3", "label": "duplicate", "unit": "percent"},
                                    {"key": "bad", "label": "Amount", "unit": "currency"}]},
        "add_groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric",
                        "kpis": [{"key": "wafers", "label": "Wafer Shipments", "unit": "count"}]}],
        "retire": ["technology.other", "technology.missing"],
    }))
    keys = {group.key: [kpi.key for kpi in group.kpis] for group in updated.groups}
    assert keys == {"technology": ["n3", "n5", "n2"], "operating": ["wafers"]}
    assert apply_maintenance(SPEC, ai.Maintenance()) is None


def test_ai_chosen_xbrl_rows_must_add_up_to_revenue():
    candidate = XbrlCandidate("geography", "Revenue by Geography", 2, "us-gaap:Revenues", 200.0, "USD", {
        "uscanada": ("US & Canada", 80.0), "europe": ("Europe", 70.0), "rest": ("Rest of World", 50.0), "us": ("U.S.", 75.0),
    })
    group = candidate.group(["uscanada", "europe", "rest"])
    assert [key for key, _label, _value in group.members] == ["uscanada", "europe", "rest"]
    assert candidate.group(["uscanada", "europe", "us"]) is None


def test_curation_asks_once_for_the_same_rows():
    candidate = XbrlCandidate("geography", "Revenue by Geography", 2, "us-gaap:Revenues", 200.0, "USD", {
        "uscanada": ("US & Canada", 80.0), "europe": ("Europe", 70.0), "rest": ("Rest of World", 50.0), "us": ("U.S.", 75.0),
    })
    control = FakeControl({("curate", "pro"): {"members": ["uscanada", "europe", "rest"]}})
    pipeline = _pipeline(control)
    assert pipeline._curate(candidate) is not None and pipeline._curate(candidate) is not None
    assert control.calls.count(("curate", "pro")) == 1


def test_jumps_stand_only_with_a_quote_found_in_the_document():
    document = parse_document("<p>Following the acquisition of Acme, subscribers rose to 90 million this quarter.</p>"
                              "<p>Subscribers: 90 million</p>", "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric",
                                            "kpis": [{"key": "subs", "label": "Subscribers", "unit": "count"}]}]})
    block = next(key for key, item in document.blocks.items() if "Subscribers:" in item.text)
    located = ai.Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "operating.subs", "block": block, "quote": "Subscribers: 90 million", "value_text": "90 million"}]})
    for quote, expected in (("Following the acquisition of Acme, subscribers rose", "verified"),
                            ("Invented sentence that is not in the filing at all", "needs_review")):
        read, _ = read_values(document, spec, located, None)
        read[0].status, read[0].notes = "needs_review", ["changed 3.0x in a quarter"]
        control = FakeControl({("explain", "pro"): {"items": {"operating.subs": {"legitimate": True, "quote": quote}}}})
        _pipeline(control)._explain_jumps(read, document, spec)
        assert read[0].status == expected


def test_furnished_filings_are_classified_once():
    ref = SimpleNamespace(confirmed=False, accession="0001-26-1", exhibits=[], form="6-K", filed=date(2026, 7, 16),
                          role="earnings_release", source_url="https://www.sec.gov/x.htm")
    control = FakeControl({("classify", "flash"): {"kind": "other"}})
    pipeline = _pipeline(control)
    assert pipeline._is_earnings_document(ref) is False
    assert pipeline._is_earnings_document(ref) is False  # the stored verdict is reused
    assert control.calls.count(("classify", "flash")) == 1


def test_large_documents_are_narrowed_to_the_sections_flash_picks():
    html = "<p>Quarter ended June 30, 2026</p>" + "".join(f"<p>Note {i}: leases and other matters.</p>" * 40 for i in range(60))
    html += "<table><tr><td>Platform</td><td>2026</td></tr><tr><td>HPC</td><td>830,369</td></tr></table>"
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table_id = next(iter(document.tables))
    control = FakeControl({("select", "flash"): {"ids": [table_id, "made_up"]}})
    pipeline = _pipeline(control)
    import kpi_extractor.pipeline as module
    limit, module.MAX_DOCUMENT_CHARS = module.MAX_DOCUMENT_CHARS, 5_000
    try:
        text = pipeline._prompt_document(document, SPEC)
    finally:
        module.MAX_DOCUMENT_CHARS = limit
    assert "HPC" in text and "Quarter ended June 30, 2026" in text and len(text) < 5_000


def test_golden_scoring():
    base = {"group_key": "kpi_t", "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30",
            "unit": "percent", "validation_status": "verified"}
    captured = {"TSM": [{**base, "kpi_key": "n3", "value": 30, "kpi_label": "3nm"},
                        {**base, "kpi_key": "n5", "value": 31, "kpi_label": "5nm"}]}
    golden = [
        {"symbol": "TSM", "period_end": "2026-06-30", "label": r"^3\s*nm", "unit": "percent", "value": 30},
        {"symbol": "TSM", "period_end": "2026-06-30", "label": r"^5\s*nm", "unit": "percent", "value": 33},
        {"symbol": "TSM", "period_end": "2026-06-30", "label": r"^7\s*nm", "unit": "percent", "value": 11},
        {"symbol": "TSM", "period_end": "2026-06-30", "label": r"headcount", "unit": "count", "value": 1, "optional": True},
    ]
    report = score(golden, captured)
    assert (report["correct"], report["wrong"], report["missing"], report["total"]) == (1, 1, 1, 3)


BREAKDOWN_RELEASE = """
<p>Revenue by segment (in millions)</p>
<table>
  <tr><td></td><td colspan="2">Q2 2026</td><td colspan="2">Q2 2025</td></tr>
  <tr><td>Cloud</td><td>$</td><td>700</td><td>$</td><td>500</td></tr>
  <tr><td>Devices</td><td>$</td><td>300</td><td>$</td><td>250</td></tr>
  <tr><td>Total revenue</td><td>$</td><td>1,000</td><td>$</td><td>750</td></tr>
</table>
"""
BREAKDOWN_SPEC = Spec.model_validate({"groups": [{"key": "segments", "label": "Revenue by Segment", "kind": "revenue_breakdown",
    "total_kpi": "total", "kpis": [{"key": "cloud", "label": "Cloud", "unit": "currency"},
                                   {"key": "devices", "label": "Devices", "unit": "currency"},
                                   {"key": "total", "label": "Total revenue", "unit": "currency"}]}]})


def _breakdown_answer():
    return {"period_end": "2026-06-30", "values": [
        {"kpi": "segments.cloud", "table": "T0", "row": 1, "col": 1, "scale": 1000000},
        {"kpi": "segments.devices", "table": "T0", "row": 2, "col": 1, "scale": 1000000},
        {"kpi": "segments.total", "table": "T0", "row": 3, "col": 1, "scale": 1000000},
    ]}


def test_breakdown_totals_do_not_block_flash_alone():
    document = parse_document(BREAKDOWN_RELEASE, "https://www.sec.gov/x.htm")
    items, _ = read_values(document, BREAKDOWN_SPEC, ai.Located.model_validate(_breakdown_answer()), "USD")
    history = [{"group_key": "kpi_segments", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": "Q1",
                "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value * 0.9,
                "locator": item.locator, "source_url": "https://www.sec.gov/x.htm"}
               for item in items if item.kpi.key != "total"]
    control = FakeControl({("locate", "flash"): _breakdown_answer()})
    pipeline = _pipeline(control, history)
    read, _, _ = pipeline._read("prompt", document, BREAKDOWN_SPEC, EXPECTED)
    assert control.calls == [("locate", "flash")] and len(read) == 3


def test_a_document_adding_up_to_full_year_revenue_is_annual():
    document = parse_document(BREAKDOWN_RELEASE, "https://www.sec.gov/x.htm")
    items, _ = read_values(document, BREAKDOWN_SPEC, ai.Located.model_validate(_breakdown_answer()), "USD")
    annual = {"group_key": "geography", "kpi_key": "us", "fiscal_year": "2026", "fiscal_period": "FY",
              "period_end": "2026-06-30", "method": "xbrl", "value": 1, "locator": {"total": 1_000_000_000}}
    assert _pipeline(FakeControl({}), [annual])._reports_full_year(items, EXPECTED) is True
    annual["locator"] = {"total": 4_000_000_000}
    assert _pipeline(FakeControl({}), [annual])._reports_full_year(items, EXPECTED) is False


def test_an_unavailable_ai_does_not_cost_the_filing_or_the_company():
    from kpi_extractor.control import ControlError

    class DownControl(FakeControl):
        def ai(self, *args, **kwargs):
            raise ControlError("ai failed (500): DeepSeek API error: 402")

    ref = SimpleNamespace(confirmed=False, accession="0001-26-2", exhibits=[], form="6-K", filed=date(2026, 7, 16),
                          role="earnings_release", source_url="https://www.sec.gov/x.htm")
    control = DownControl({})
    pipeline = _pipeline(control)
    assert pipeline._is_earnings_document(ref) is False
    assert ("store", None) not in control.calls  # not recorded, so the next run classifies it again
    candidate = XbrlCandidate("geography", "Revenue by Geography", 2, "us-gaap:Revenues", 200.0, "USD",
                              {"a": ("A", 80.0), "b": ("B", 70.0), "c": ("C", 60.0)})
    try:
        pipeline._curate(candidate)
    except ControlError:
        pass
    else:
        raise AssertionError("curation must surface the outage so the filing is retried")


def test_a_text_block_cited_as_a_table_is_retried_with_how_to_quote_it():
    from kpi_extractor.pipeline import _explain_pointer
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    block = next(iter(document.blocks))
    message = _explain_pointer(f"technology.n3: unknown table {block}", document)
    assert f'"block": "{block}"' in message and "not a table" in message
    assert _explain_pointer("technology.n3: unknown table A_T9", document).endswith("the document's tables are none")


def test_a_re_read_withdraws_what_the_earlier_reading_of_that_filing_had():
    from kpi_extractor.sec import FilingRef
    control = FakeControl({})
    old = {"group_key": "kpi_revenue_by_product", "kpi_key": "systems", "fiscal_year": "2026", "fiscal_period": "Q2",
           "period_end": "2026-06-30", "method": "ai", "validation_status": "verified", "value": 5.0,
           "source_accession": "a1", "group_label": "x", "kpi_label": "x"}
    pipeline = _pipeline(control, [old])
    ref = FilingRef("a1", "6-K", date(2026, 7, 15), "earnings_release", "https://www.sec.gov/x.htm")
    new = {**old, "group_key": "kpi_revenue_by_product_line", "value": 6.0}
    pipeline._store_filing(ref, "processed", records=[new])
    assert pipeline.values[("kpi_revenue_by_product", "systems", "2026", "Q2")]["validation_status"] == "rejected"
    assert pipeline.values[("kpi_revenue_by_product_line", "systems", "2026", "Q2")]["validation_status"] == "verified"
    assert pipeline.filings["a1"]["value_count"] == 1


def test_a_quarter_is_compared_with_the_last_quarter_not_the_full_year():
    base = {"group_key": "kpi_product", "kpi_key": "systems", "method": "ai", "validation_status": "verified"}
    pipeline = _pipeline(FakeControl({}), [
        {**base, "fiscal_year": "2025", "fiscal_period": "Q3", "period_end": "2025-09-28", "value": 5553.8},
        {**base, "fiscal_year": "2025", "fiscal_period": "FY", "period_end": "2025-12-31", "value": 24474.3},
    ])
    assert pipeline._previous(date(2026, 3, 29)) == {}  # Q3 is outside the quarter window; FY never counts
    assert pipeline._previous(date(2025, 12, 31)) == {"product.systems": 5553.8}
    assert pipeline._previous(date(2026, 12, 31), annual=True) == {"product.systems": 24474.3}


def test_a_re_proposed_list_keeps_the_groups_it_dropped():
    from kpi_extractor.pipeline import keep_current_groups
    current = Spec.model_validate({"groups": [
        {"key": "technology", "label": "Revenue by Technology", "kind": "mix", "kpis": [{"key": "n3", "label": "3nm", "unit": "percent"}]},
        {"key": "platform", "label": "Revenue by Platform", "kind": "mix", "kpis": [{"key": "hpc", "label": "HPC", "unit": "percent"}]},
    ]})
    proposed = Spec.model_validate({"groups": [
        {"key": "tech", "label": "Revenue by technology", "kind": "mix", "kpis": [{"key": "n3", "label": "3nm", "unit": "percent"}]},
    ]})
    assert [group.key for group in keep_current_groups(proposed, current).groups] == ["tech", "platform"]


def test_the_audit_never_adds_a_mix_of_a_breakdown_tracked_as_amounts():
    spec = Spec.model_validate({"groups": [{"key": "technology", "label": "Revenue by Technology", "kind": "revenue_breakdown",
                                            "kpis": [{"key": "n3", "label": "3nm", "unit": "currency"}]}]})
    answer = ai.Maintenance.model_validate({"add_groups": [
        {"key": "technology_mix", "label": "Revenue by Technology Mix", "kind": "mix",
         "kpis": [{"key": "n3", "label": "3nm", "unit": "percent"}]}]})
    assert apply_maintenance(spec, answer) is None


def test_a_fast_growers_real_q4_is_not_taken_for_a_year():
    base = {"group_key": "kpi_product", "kpi_key": "a", "method": "ai", "validation_status": "verified", "value": 1.0,
            "group_label": "x", "kpi_label": "x", "source_accession": "x"}
    history = [{**base, "fiscal_year": str(2021 + i // 4), "fiscal_period": f"Q{i % 4 + 1}", "period_end": end,
                "locator": {"total": total}}
               for i, (end, total) in enumerate([("2021-03-31", 100), ("2021-06-30", 100), ("2021-09-30", 100),
                                                 ("2026-03-31", 270), ("2026-06-30", 280), ("2026-09-30", 290)])]
    history.append({**base, "fiscal_year": "2026", "fiscal_period": "Q4", "period_end": "2026-12-31",
                    "source_accession": "q4", "locator": {"total": 300}})
    pipeline = _pipeline(FakeControl({}), history)
    pipeline._refile_annuals()
    assert pipeline.values[("kpi_product", "a", "2026", "Q4")]["validation_status"] == "verified"


def test_one_business_keeps_one_series_across_renamed_and_reused_elements():
    def row(year, period, end, key, value, element, prior=None):
        return {"group_key": "products", "kpi_key": key, "fiscal_year": year, "fiscal_period": period, "period_end": end,
                "method": "xbrl", "validation_status": "verified", "value": value, "source_accession": f"{year}{period}",
                "group_label": "Revenue by Product", "kpi_label": key, "group_kind": "revenue_breakdown",
                "locator": {"member": element, "prior": prior}}
    history = [
        row("2021", "FY", "2021-06-30", "searchadvertising", 9.0, "msft:SearchAdvertisingMember"),
        row("2025", "Q3", "2025-03-31", "searchandnewsadvertising", 3504.0, "msft:SearchAndNewsAdvertisingMember"),
        row("2026", "Q2", "2025-12-31", "searchandnewsadvertising", 3810.0, "msft:SearchAndNewsAdvertisingMember"),
        row("2026", "Q3", "2026-03-31", "searchadvertising", 3808.0, "msft:SearchAdvertisingMember", prior=3504.0),
        row("2026", "Q3", "2026-03-31", "linkedin", 4832.0, "msft:LinkedInCorporationMember"),
    ]
    pipeline = _pipeline(FakeControl({}), history)
    pipeline._unify_series()
    live = {(v["fiscal_year"], v["fiscal_period"]): v["kpi_key"] for v in pipeline.values.values()
            if v["validation_status"] != "rejected" and v["kpi_key"] != "linkedin"}
    assert set(live.values()) == {"searchadvertising"} and len(live) == 4


def test_a_breakdown_that_can_never_reconcile_becomes_metrics():
    spec = Spec.model_validate({"groups": [
        {"key": "product_line", "label": "Revenue by Product Line", "kind": "revenue_breakdown",
         "kpis": [{"key": "ai_semiconductor", "label": "AI Semiconductor", "unit": "currency"}]},
        {"key": "segments", "label": "Revenue by Segment", "kind": "revenue_breakdown", "total_kpi": "total",
         "kpis": [{"key": "a", "label": "A", "unit": "currency"}, {"key": "b", "label": "B", "unit": "currency"},
                  {"key": "total", "label": "Total", "unit": "currency"}]},
    ]})
    normalized = ai.normalize_spec(spec)
    kinds = {group.key: (group.kind, [kpi.key for kpi in group.kpis]) for group in normalized.groups}
    assert kinds == {"segments": ("revenue_breakdown", ["a", "b", "total"]),
                     "operating": ("metric", ["ai_semiconductor"])}


def test_stored_whole_table_rows_move_to_the_listed_keys():
    spec = Spec.model_validate({"groups": [{"key": "technology", "label": "Revenue by Technology", "kind": "revenue_breakdown",
                                            "total_kpi": "wafer", "kpis": [
        {"key": "nm3", "label": "3nm", "unit": "currency"}, {"key": "nm5", "label": "5nm", "unit": "currency"},
        {"key": "wafer", "label": "Wafer revenue", "unit": "currency"}]}]})
    base = {"group_key": "kpi_technology", "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30",
            "method": "ai", "group_kind": "revenue_breakdown", "group_label": "Revenue by Technology", "source_accession": "r"}
    rows = [{**base, "kpi_key": "r3nanometer", "kpi_label": "3-nanometer", "value": 320.0, "validation_status": "verified"},
            {**base, "kpi_key": "nm3", "kpi_label": "3nm", "value": 30.0, "validation_status": "needs_review",
             "source_accession": "release"}]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._adopt_listed_keys(spec)
    assert pipeline.values[("kpi_technology", "nm3", "2026", "Q2")]["value"] == 320.0
    assert pipeline.values[("kpi_technology", "r3nanometer", "2026", "Q2")]["validation_status"] == "rejected"


def test_an_audit_that_only_adds_is_recognized():
    from kpi_extractor.pipeline import only_adds
    added = apply_maintenance(SPEC, ai.Maintenance.model_validate({
        "add_kpis": {"technology": [{"key": "n2", "label": "2nm", "unit": "percent"}]}}))
    retired = apply_maintenance(SPEC, ai.Maintenance.model_validate({"retire": ["technology.other"]}))
    assert only_adds(SPEC, added) and not only_adds(SPEC, retired)


def test_a_failed_re_read_keeps_the_filings_good_values():
    from kpi_extractor.sec import FilingRef
    good = {"group_key": "kpi_segments", "kpi_key": "cloud", "fiscal_year": "2026", "fiscal_period": "Q2",
            "period_end": "2026-06-30", "method": "ai", "validation_status": "verified", "value": 5.0,
            "source_accession": "a1", "group_label": "x", "kpi_label": "x"}
    pipeline = _pipeline(FakeControl({}), [good])
    ref = FilingRef("a1", "8-K", date(2026, 7, 15), "earnings_release", "https://www.sec.gov/x.htm")
    pipeline._fail_filing(ref, RuntimeError("AI unavailable"))
    assert pipeline.values[("kpi_segments", "cloud", "2026", "Q2")]["validation_status"] == "verified"


def test_a_big_q4_is_never_moved_to_the_year_by_size_alone():
    base = {"group_key": "kpi_product", "kpi_key": "a", "method": "ai", "validation_status": "verified", "value": 1.0,
            "group_label": "x", "kpi_label": "x", "group_kind": "revenue_breakdown"}
    rows = [{**base, "fiscal_year": "2026", "fiscal_period": "Q3", "period_end": "2026-09-30", "source_accession": "q3",
             "locator": {"total": 100, "header": "three months ended @ # #"}},
            {**base, "fiscal_year": "2026", "fiscal_period": "Q4", "period_end": "2026-12-31", "source_accession": "q4",
             "locator": {"total": 260, "header": "three months ended @ # #"}}]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._refile_annuals()
    assert pipeline.values[("kpi_product", "a", "2026", "Q4")]["validation_status"] == "verified"




def test_carried_checkpoints_are_complete_for_the_request_check():
    from kpi_extractor.sec import FilingRef
    sent = []

    class Capture(FakeControl):
        def call(self, operation, **payload):
            sent.append(payload)
            return {}
    pipeline = _pipeline(Capture({}), [])
    pipeline.filings = {"a1": {"accession": "a1", "status": "processed", "attempts": 0, "value_count": 3, "notes": [],
                               "period_end": "2025-06-30", "spec_version": 4}}
    ref = FilingRef("a1", "6-K", date(2025, 7, 17), "earnings_release", "https://www.sec.gov/x.htm")
    pipeline._carry_to_version(ref, 5)
    filing = sent[-1]["filings"][0]
    assert {"form", "filed_at", "document_role", "status"} <= filing.keys() and filing["attempts"] >= 1
    assert filing["document_role"] in ("periodic_report", "earnings_release") and filing["spec_version"] == 5


def test_one_flagged_part_flags_the_whole_breakdown():
    from kpi_extractor.extract import ReadValue
    from kpi_extractor.sec import FilingRef
    group = SPEC.groups[0]
    read = [ReadValue(group, group.kpis[0], 30.0, None, {}), ReadValue(group, group.kpis[1], 33.0, None, {}),
            ReadValue(group, group.kpis[2], 37.0, None, {}, status="needs_review", notes=["moved 31 points"])]
    pipeline = _pipeline(FakeControl({}), [])
    ref = FilingRef("a1", "6-K", date(2026, 7, 16), "earnings_release", "https://www.sec.gov/x.htm")
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    records = pipeline._release_records(read, SPEC, ref, document, "2026", "Q2", EXPECTED, None)
    assert {record["validation_status"] for record in records} == {"needs_review"}


def test_the_split_the_quarters_use_is_kept():
    from kpi_extractor.xbrl import XbrlGroup
    base = {"group_key": "geography", "fiscal_year": "2025", "fiscal_period": "Q3", "period_end": "2025-08-03",
            "method": "xbrl", "validation_status": "verified", "value": 1.0, "source_accession": "q3"}
    pipeline = _pipeline(FakeControl({}), [{**base, "kpi_key": k} for k in ("americas", "asiapacific", "emea")])
    group = XbrlGroup("geography", "Revenue by Geography", 2, [("china", "China", 11.0), ("singapore", "Singapore", 10.8),
                                                               ("unitedstates", "United States", 16.5), ("other", "Other", 25.6)],
                      63.9, 0.0, "USD", "c", alternative=[("americas", "Americas", 20.0), ("asiapacific", "Asia Pacific", 34.9),
                                                          ("emea", "EMEA", 9.0)])
    chosen = pipeline._continuing_split(group, date(2025, 11, 2))
    assert [key for key, _l, _v in chosen.members] == ["americas", "asiapacific", "emea"]


def test_an_ai_breakdown_must_total_a_reported_figure():
    base = {"fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-27", "group_label": "x", "kpi_label": "x",
            "group_kind": "revenue_breakdown", "source_accession": "a"}
    xbrl = [{**base, "group_key": "segments", "kpi_key": "clientandgaming", "method": "xbrl", "value": 3841.0,
             "validation_status": "verified", "locator": {"total": 11563.0}}]
    good = [{**base, "group_key": "kpi_cg", "kpi_key": k, "method": "ai", "value": v, "validation_status": "verified",
             "locator": {"total": 3841.0}} for k, v in (("client", 3062.0), ("gaming", 779.0))]
    bad = [{**base, "group_key": "kpi_wrong", "kpi_key": k, "method": "ai", "value": v, "validation_status": "verified",
            "locator": {"total": 9000.0}} for k, v in (("a", 5000.0), ("b", 4000.0))]
    pipeline = _pipeline(FakeControl({}), xbrl + good + bad)
    pipeline._anchor_ai_breakdowns()
    assert pipeline.values[("kpi_cg", "client", "2026", "Q2")]["validation_status"] == "verified"
    assert pipeline.values[("kpi_wrong", "a", "2026", "Q2")]["validation_status"] == "needs_review"


def test_the_year_ago_column_confirms_a_reading_in_a_new_place():
    html = """<table><tr><td></td><td>Three Months Ended June 30, 2026</td><td>Three Months Ended June 30, 2025</td></tr>
      <tr><td>Client</td><td>3,062</td><td>2,499</td></tr><tr><td>Gaming</td><td>779</td><td>1,122</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating Metrics", "kind": "metric", "kpis": [
        {"key": "client", "label": "Client", "unit": "count"}, {"key": "gaming", "label": "Gaming", "unit": "count"}]}]})
    located = ai.Located.model_validate({"period_end": "2026-06-30", "values": [
        {"kpi": "ops.client", "table": table, "row": 1, "col": 1}, {"kpi": "ops.gaming", "table": table, "row": 2, "col": 1}]})
    read, _ = read_values(document, spec, located, "USD")
    base = {"group_key": "kpi_ops", "fiscal_year": "2025", "fiscal_period": "Q2", "period_end": "2025-06-28", "method": "ai",
            "validation_status": "verified"}
    pipeline = _pipeline(FakeControl({}), [{**base, "kpi_key": "client", "value": 2499.0}, {**base, "kpi_key": "gaming", "value": 1122.0}])
    assert pipeline._year_ago_confirms(read, document, EXPECTED)
    pipeline = _pipeline(FakeControl({}), [{**base, "kpi_key": "client", "value": 2499.0}, {**base, "kpi_key": "gaming", "value": 999.0}])
    assert not pipeline._year_ago_confirms(read, document, EXPECTED)


def test_quarters_a_filer_mislabelled_are_relabelled_from_later_comparatives():
    def row(year, period, end, key, value, prior=None, label=None):
        return {"group_key": "segments", "kpi_key": key, "kpi_label": label or key, "fiscal_year": year, "fiscal_period": period,
                "period_end": end, "method": "xbrl", "validation_status": "verified", "value": value, "source_accession": f"{year}{period}",
                "group_label": "Revenue by Segment", "group_kind": "revenue_breakdown", "locator": {"prior": prior}}
    # AMD's 2024 10-Qs: Data Center's figures tagged Client, Client's Gaming, Gaming's Data Center.
    tagged = {"Q1": ("2024-03-30", 2337, 922, 1368, 846), "Q2": ("2024-06-29", 2834, 648, 1492, 861),
              "Q3": ("2024-09-28", 3549, 462, 1881, 927)}
    history = []
    for period, (end, client, datacenter, gaming, embedded) in tagged.items():
        history += [row("2024", period, end, "client", client), row("2024", period, end, "datacenter", datacenter),
                    row("2024", period, end, "gaming", gaming), row("2024", period, end, "embedded", embedded)]
    history += [row("2024", "FY", "2024-12-28", k, v) for k, v in (("client", 7054), ("datacenter", 12579), ("gaming", 2595), ("embedded", 3557))]
    # The 2025 10-Qs restate Data Center and Embedded correctly (Client and Gaming are combined by then).
    later = {"Q1": ("2025-03-29", 2337, 846), "Q2": ("2025-06-28", 2834, 861), "Q3": ("2025-09-27", 3549, 927)}
    for period, (end, datacenter, embedded) in later.items():
        history += [row("2025", period, end, "datacenter", 1, prior=datacenter), row("2025", period, end, "embedded", 1, prior=embedded)]
    pipeline = _pipeline(FakeControl({}), history)
    pipeline._fix_mislabelled_quarters()
    q1 = {k: v["value"] for (g, k, y, p), v in pipeline.values.items() if y == "2024" and p == "Q1" and v["validation_status"] == "verified"}
    assert q1 == {"datacenter": 2337, "client": 1368, "gaming": 922, "embedded": 846}


def test_a_total_pointing_at_no_kpi_does_not_discard_the_list():
    spec = Spec.model_validate({"groups": [{"key": "product", "label": "Revenue by Product", "kind": "revenue_breakdown",
                                            "total_kpi": "total_revenues", "kpis": [
        {"key": "search", "label": "Search", "unit": "currency"}, {"key": "youtube", "label": "YouTube ads", "unit": "currency"}]}]})
    assert spec.groups[0].total_kpi is None and len(spec.groups[0].kpis) == 2


def test_parts_held_for_another_part_are_released_once_it_clears():
    base = {"group_key": "kpi_technology", "group_kind": "revenue_breakdown", "fiscal_year": "2024", "fiscal_period": "FY",
            "period_end": "2024-12-31", "method": "ai", "source_accession": "annual", "group_label": "x", "kpi_label": "x",
            "locator": {"total": 100.0, "whole_table": True}}
    rows = [{**base, "kpi_key": "nm3", "value": 18.0, "validation_status": "verified", "notes": ["the year's breakdown reconciles"]},
            {**base, "kpi_key": "nm5", "value": 82.0, "validation_status": "needs_review",
             "notes": ["another part of this breakdown needs review"]}]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._release_held_breakdowns()
    assert pipeline.values[("kpi_technology", "nm5", "2024", "FY")]["validation_status"] == "verified"


def test_the_year_ago_check_needs_every_value_and_a_column_of_the_same_length():
    html = """<table><tr><td></td><td>Three Months Ended June 30, 2026</td><td>Six Months Ended June 30, 2026</td>
      <td>Three Months Ended June 30, 2025</td></tr>
      <tr><td>Client</td><td>3,062</td><td>5,947</td><td>2,499</td></tr><tr><td>Gaming</td><td>779</td><td>1,499</td><td>1,122</td></tr>
      <tr><td>Embedded</td><td>977</td><td>1,850</td><td>824</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Operating Metrics", "kind": "metric", "kpis": [
        {"key": k, "label": k.title(), "unit": "count"} for k in ("client", "gaming", "embedded")]}]})
    def reading(col):
        located = ai.Located.model_validate({"period_end": "2026-06-30", "values": [
            {"kpi": f"ops.{k}", "table": table, "row": r, "col": col} for k, r in (("client", 1), ("gaming", 2), ("embedded", 3))]})
        return read_values(document, spec, located, "USD")[0]
    base = {"group_key": "kpi_ops", "fiscal_year": "2025", "fiscal_period": "Q2", "period_end": "2025-06-28", "method": "ai",
            "validation_status": "verified"}
    stored = [{**base, "kpi_key": "client", "value": 2499.0}, {**base, "kpi_key": "gaming", "value": 1122.0}]
    # Embedded has no stored year-ago figure and no place last quarter: not accounted for.
    assert not _pipeline(FakeControl({}), stored)._year_ago_confirms(reading(1), document, EXPECTED)
    stored.append({**base, "kpi_key": "embedded", "value": 824.0})
    assert _pipeline(FakeControl({}), stored)._year_ago_confirms(reading(1), document, EXPECTED)
    # The six-month column's row also holds last year's quarter, but in a column of another length.
    assert not _pipeline(FakeControl({}), stored)._year_ago_confirms(reading(2), document, EXPECTED)


def test_a_breakdown_of_one_segment_is_titled_for_it():
    base = {"fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-27", "kpi_label": "x",
            "group_kind": "revenue_breakdown", "source_accession": "a"}
    xbrl = [{**base, "group_key": "segments", "group_label": "Revenue by Segment", "kpi_key": "clientandgaming",
             "kpi_label": "Client and Gaming", "method": "xbrl", "value": 3841.0, "validation_status": "verified",
             "locator": {"total": 11563.0}}]
    ai_rows = [{**base, "group_key": "kpi_cg", "group_label": "Revenue by Product", "kpi_key": k, "method": "ai", "value": v,
                "validation_status": "verified", "locator": {"total": 3841.0}} for k, v in (("client", 3062.0), ("gaming", 779.0))]
    pipeline = _pipeline(FakeControl({}), xbrl + ai_rows)
    pipeline._anchor_ai_breakdowns()
    assert pipeline.values[("kpi_cg", "client", "2026", "Q2")]["group_label"] == "Client and Gaming Revenue by Product"


def _flagged_fixture(html, flagged):
    """A pipeline holding this year's verified readings of a release table and last year's flagged values."""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    common = {"group_key": "kpi_ops", "group_kind": "metric", "group_label": "Ops", "unit": "count", "method": "ai"}
    later = [{**common, "kpi_key": key, "kpi_label": key, "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30",
              "validation_status": "verified", "source_accession": "later", "value": value,
              "locator": {"table": table, "row": row, "col": 1}} for key, row, value in (("client", 1, 3062.0), ("gaming", 2, 779.0))]
    earlier = [{**common, "kpi_key": key, "kpi_label": key, "fiscal_year": "2025", "fiscal_period": "Q2", "period_end": "2025-06-28",
                "validation_status": "needs_review", "source_accession": "earlier", "value": value, "notes": ["changed 3x in a quarter"]}
               for key, value in flagged.items()]
    pipeline = _pipeline(FakeControl({}), later + earlier)
    pipeline._document = lambda ref: document
    pipeline._prove_flagged([SimpleNamespace(accession="later")])
    return {k: v for (g, k, y, p), v in pipeline.values.items() if y == "2025"}


def test_the_next_years_release_confirms_or_restates_a_flagged_value():
    html = """<table><tr><td></td><td>Three Months Ended June 30, 2026</td><td>Three Months Ended June 30, 2025</td></tr>
      <tr><td>Client</td><td>3,062</td><td>2,499</td></tr><tr><td>Gaming</td><td>779</td><td>1,122</td></tr></table>"""
    settled = _flagged_fixture(html, {"client": 2499.0, "gaming": 1100.0})
    assert settled["client"]["validation_status"] == "verified"
    assert settled["gaming"]["validation_status"] == "verified" and settled["gaming"]["value"] == 1122.0
    # A figure far from the flagged one is another column (a year beside a quarter), not a restatement.
    assert _flagged_fixture(html, {"client": 2499.0, "gaming": 500.0})["gaming"]["validation_status"] == "needs_review"


def test_a_flagged_value_no_column_of_the_next_years_release_shows_is_rejected():
    html = """<table><tr><td></td><td>Three Months Ended June 30, 2026</td><td>Three Months Ended March 31, 2026</td>
      <td>Three Months Ended June 30, 2025</td></tr>
      <tr><td>Client</td><td>3,062</td><td>2,900</td><td>2,499</td></tr><tr><td>Gaming</td><td>779</td><td>700</td><td>1,122</td></tr></table>"""
    settled = _flagged_fixture(html, {"client": 2499.0, "gaming": 1000.0})
    assert settled["client"]["validation_status"] == "verified"
    assert settled["gaming"]["validation_status"] == "rejected"


def test_a_flagged_value_equal_to_an_official_figure_is_proven_only_when_precise():
    xbrl = {"group_key": "segments", "group_kind": "revenue_breakdown", "method": "xbrl", "validation_status": "verified",
            "fiscal_year": "2025", "fiscal_period": "Q2", "period_end": "2025-06-28", "locator": {}}
    ai_row = {"group_key": "kpi_product", "group_kind": "revenue_breakdown", "method": "ai", "validation_status": "needs_review",
              "fiscal_year": "2025", "fiscal_period": "Q2", "period_end": "2025-06-28", "unit": "currency",
              "notes": ["no total revenue to reconcile against"], "source_accession": "r"}
    pipeline = _pipeline(FakeControl({}), [
        {**xbrl, "kpi_key": "datacenter", "value": 3_859_000_000.0}, {**xbrl, "kpi_key": "round", "value": 60_000_000_000.0},
        {**ai_row, "kpi_key": "datacenter", "value": 3_859_000_000.0}, {**ai_row, "group_key": "kpi_other", "kpi_key": "round",
                                                                         "value": 60_000_000_000.0}])
    pipeline._prove_flagged([])
    assert pipeline.values[("kpi_product", "datacenter", "2025", "Q2")]["validation_status"] == "verified"
    assert pipeline.values[("kpi_other", "round", "2025", "Q2")]["validation_status"] == "needs_review"


def test_reports_reach_ten_years_while_releases_keep_the_runs_window():
    pipeline = SymbolPipeline(FakeControl({}), "NVDA", quarters=20, force=False, today=date(2026, 10, 2),
                              log=lambda *_: None, report_quarters=40)
    assert pipeline.since.year == 2021 and pipeline.reports_since.year == 2016
    assert SymbolPipeline(FakeControl({}), "NVDA", quarters=6, force=False, today=date(2026, 10, 2),
                          log=lambda *_: None).reports_since == date(2026, 10, 2) - __import__("datetime").timedelta(days=92 * 6 + 120)


def test_an_early_custom_axis_joins_the_standard_group_it_became():
    def row(group, key, year, period, end, value):
        return {"group_key": group, "kpi_key": key, "kpi_label": key, "fiscal_year": year, "fiscal_period": period,
                "period_end": end, "method": "xbrl", "validation_status": "verified", "value": value,
                "group_label": "Revenue by Product" if group == "products" else "Revenue by Major Market",
                "group_kind": "revenue_breakdown", "group_order": 1, "locator": {}}
    old = [row("x_revenuebymajormarket", key, "2018", "Q1", "2017-04-30", value)
           for key, value in (("gaming", 1027.0), ("datacenter", 409.0), ("automotive", 140.0))]
    new = [row("products", key, "2019", "Q1", "2018-04-29", value)
           for key, value in (("gaming", 1723.0), ("datacenter", 701.0), ("automotive", 145.0), ("oemandip", 387.0))]
    pipeline = _pipeline(FakeControl({}), old + new)
    pipeline._join_custom_axes()
    joined = {k for (g, k, y, p), v in pipeline.values.items() if g == "products" and y == "2018" and v["validation_status"] == "verified"}
    assert joined == {"gaming", "datacenter", "automotive"}
    assert all(v["validation_status"] == "rejected" for (g, *_), v in pipeline.values.items() if g == "x_revenuebymajormarket")


def test_a_report_read_by_an_older_xbrl_reader_is_read_again():
    from kpi_extractor.pipeline import _XBRL_READER
    pipeline = _pipeline(FakeControl({}))
    ref = SimpleNamespace(accession="a", role="periodic_report")
    pipeline.filings = {"a": {"status": "processed", "attempts": 1, "notes": []}}
    assert pipeline._pending(ref)
    pipeline.filings = {"a": {"status": "processed", "attempts": 1, "notes": [_XBRL_READER]}}
    assert not pipeline._pending(ref)
    pipeline.filings = {"a": {"status": "processed", "attempts": 1, "notes": []}}
    assert not pipeline._pending(SimpleNamespace(accession="a", role="earnings_release"))


def test_a_part_equal_to_xbrl_is_not_proven_in_a_breakdown_that_misses_its_total():
    xbrl = {"group_key": "segments", "group_kind": "revenue_breakdown", "method": "xbrl", "validation_status": "verified",
            "fiscal_year": "2025", "fiscal_period": "Q4", "period_end": "2025-12-31", "locator": {}}
    part = {"group_key": "kpi_segment", "group_kind": "revenue_breakdown", "method": "ai", "fiscal_year": "2025",
            "fiscal_period": "Q4", "period_end": "2025-12-31", "unit": "currency", "source_accession": "r",
            "locator": {"total": 113_215_000_000.0}}
    pipeline = _pipeline(FakeControl({}), [
        {**xbrl, "kpi_key": "uhc", "value": 87_113_000_000.0}, {**xbrl, "kpi_key": "optumrx", "value": 41_456_000_000.0},
        {**part, "kpi_key": "uhc", "value": 87_113_000_000.0, "validation_status": "needs_review", "notes": ["parts differ"]},
        {**part, "kpi_key": "optumrx", "value": 41_456_000_000.0, "validation_status": "verified",
         "notes": ["equals a figure reported in XBRL"]}])
    pipeline._prove_flagged([])
    assert pipeline.values[("kpi_segment", "uhc", "2025", "Q4")]["validation_status"] == "needs_review"
    assert pipeline.values[("kpi_segment", "optumrx", "2025", "Q4")]["validation_status"] == "needs_review"


def test_a_renamed_release_line_joins_its_series_by_the_year_ago_cell():
    html = """<table><tr><td></td><td>Three Months Ended December 31, 2023</td><td>Three Months Ended December 31, 2022</td></tr>
      <tr><td>Google subscriptions, platforms, and devices</td><td>10,794</td><td>8,796</td></tr>
      <tr><td>Google Cloud</td><td>9,192</td><td>7,315</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    def row(key, year, period, end, value, accession="old", locator=None):
        return {"group_key": "kpi_product", "group_kind": "revenue_breakdown", "group_label": "Revenue by Product",
                "kpi_key": key, "kpi_label": key, "fiscal_year": year, "fiscal_period": period, "period_end": end,
                "method": "ai", "validation_status": "verified", "value": value, "source_accession": accession,
                "locator": locator or {}, "unit": "currency"}
    rows = [row("googleother", "2022", "Q4", "2022-12-31", 8_796.0), row("googleother", "2023", "Q3", "2023-09-30", 8_005.0),
            row("cloud", "2022", "Q4", "2022-12-31", 7_315.0), row("cloud", "2023", "Q4", "2023-12-31", 9_192.0, "new"),
            row("subscriptions", "2023", "Q4", "2023-12-31", 10_794.0, "new", {"table": table, "row": 1, "col": 1})]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._document = lambda ref: document
    pipeline._unify_release_series([SimpleNamespace(accession="new")])
    renamed = {(y, p) for (g, k, y, p), v in pipeline.values.items() if k == "subscriptions" and v["validation_status"] == "verified"}
    assert renamed == {("2022", "Q4"), ("2023", "Q3"), ("2023", "Q4")}


def test_a_quarter_on_an_older_layout_takes_the_following_years_restatement():
    def row(key, year, period, end, value, prior=None, total=None, accession="a"):
        return {"group_key": "products", "group_kind": "revenue_breakdown", "group_label": "Revenue by Product", "kpi_key": key,
                "kpi_label": key, "fiscal_year": year, "fiscal_period": period, "period_end": end, "method": "xbrl",
                "validation_status": "verified", "value": value, "source_accession": accession,
                "locator": {"total": total, **({"prior": prior} if prior is not None else {})}}
    values = [row("search", "2019", "FY", "2019-12-31", 98.0, total=120.0), row("youtube", "2019", "FY", "2019-12-31", 22.0, total=120.0),
              row("properties", "2019", "Q2", "2019-06-30", 30.0, total=30.0),  # Search and YouTube together
              row("search", "2020", "Q2", "2020-06-30", 26.0, prior=24.5, total=32.0, accession="b"),
              row("youtube", "2020", "Q2", "2020-06-30", 6.0, prior=5.5, total=32.0, accession="b")]
    pipeline = _pipeline(FakeControl({}), values)
    pipeline._adopt_restated_quarters()
    q2 = {k: v["value"] for (g, k, y, p), v in pipeline.values.items() if y == "2019" and p == "Q2" and v["validation_status"] == "verified"}
    assert q2 == {"search": 24.5, "youtube": 5.5}
    assert pipeline.values[("products", "properties", "2019", "Q2")]["validation_status"] == "rejected"


def test_a_quarter_finer_than_its_year_keeps_its_own_rows():
    def row(key, year, period, end, value, prior=None, total=None, accession="a"):
        return {"group_key": "geography", "group_kind": "revenue_breakdown", "group_label": "Revenue by Geography", "kpi_key": key,
                "kpi_label": key, "fiscal_year": year, "fiscal_period": period, "period_end": end, "method": "xbrl",
                "validation_status": "verified", "value": value, "source_accession": accession,
                "locator": {"total": total, **({"prior": prior} if prior is not None else {})}}
    values = [row("us", "2026", "FY", "2026-01-25", 60.0, total=100.0), row("other", "2026", "FY", "2026-01-25", 40.0, total=100.0),
              row("us", "2026", "Q1", "2025-04-27", 15.0, total=25.0), row("singapore", "2026", "Q1", "2025-04-27", 4.0, total=25.0),
              row("other", "2026", "Q1", "2025-04-27", 6.0, total=25.0),
              row("us", "2027", "Q1", "2026-04-26", 20.0, prior=15.0, total=30.0, accession="b"),
              row("other", "2027", "Q1", "2026-04-26", 10.0, prior=10.0, total=30.0, accession="b")]
    pipeline = _pipeline(FakeControl({}), values)
    pipeline._adopt_restated_quarters()
    assert pipeline.values[("geography", "singapore", "2026", "Q1")]["validation_status"] == "verified"


def test_an_overlong_kpi_key_is_repaired_instead_of_discarding_the_list():
    from kpi_extractor.ai import KpiSpec
    assert KpiSpec(key="global_corporate_banking_global_investment_banking", label="x", unit="currency").key == \
        "global_corporate_banking_global_investment_banki"
    assert KpiSpec(key="3nm Share", label="x", unit="percent").key == "k_3nm_share"
