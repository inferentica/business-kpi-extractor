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
    assert _explain_pointer("technology.n3: unknown table A_T9", document) == "technology.n3: unknown table A_T9"


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
