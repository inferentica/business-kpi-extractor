import json
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.document import parse_document
from kpi_extractor.evaluate import score
from kpi_extractor.extract import read_values
from kpi_extractor.pipeline import SymbolPipeline, _template, apply_maintenance, without_xbrl_repeats
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
        # Every call is Flash; a reasoning call (the second reading, a review) answers as "reasoning".
        reader = "reasoning" if thinking else model
        self.calls.append((purpose, reader))
        return json.dumps(self.answers[(purpose, reader)])

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
    control = FakeControl({("locate", "flash"): _answer(document), ("locate", "reasoning"): _answer(document)})
    read, _problems, _located = _pipeline(control)._read("prompt", document, SPEC, EXPECTED)
    assert {item.kpi.key: item.value for item in read} == {"n3": 30, "n5": 33, "other": 37}
    # The rest gets the second, reasoning reading; nothing needs a review.
    assert ("locate", "reasoning") in control.calls and not any(purpose == "review" for purpose, _model in control.calls)


def test_flash_alone_when_everything_is_where_it_was_last_quarter():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    control = FakeControl({("locate", "flash"): _answer(document)})
    pipeline = _pipeline(control, _history(document, _answer(document)))
    read, _, _ = pipeline._read("prompt", document, SPEC, EXPECTED)
    assert len(read) == 3
    assert control.calls == [("locate", "flash")]
    assert pipeline.result.flash_only_reads == 1


def test_a_reading_in_a_new_place_brings_in_pro_and_the_checks_settle_it():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    control = FakeControl({
        ("locate", "flash"): _answer(document, "3-nanometer accounted for 20%", "20%"),
        ("locate", "reasoning"): _answer(document),
        ("review", "reasoning"): {"choices": {"technology.n3": "B"}},
    })
    history = _history(document, _answer(document))
    history[0]["locator"] = {"block": "P9", "quote": "Leading-edge 3-nanometer share: 29%", "value_text": "29%"}
    read, _, _ = _pipeline(control, history)._read("prompt", document, SPEC, EXPECTED)
    values = {item.kpi.key: item for item in read}
    # Flash's 20% (the year-ago share) leaves the mix at 90%; Pro's 30% makes it whole: no review needed.
    assert values["n3"].value == 30 and "settled by the checks" in values["n3"].notes
    assert ("review", "reasoning") not in control.calls


def test_review_rejecting_both_readings_drops_the_kpi():
    document = parse_document(RELEASE, "https://www.sec.gov/x.htm")
    flash = {"period_end": "2026-06-30", "values": [_quote(document, "technology.n3", "3-nanometer accounted for 20%", "20%")]}
    control = FakeControl({
        ("locate", "flash"): flash,
        ("locate", "reasoning"): {"period_end": "2026-06-30", "values": []},
        ("review", "reasoning"): {"choices": {"technology.n3": "none"}},
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
    control = FakeControl({("curate", "reasoning"): {"members": ["uscanada", "europe", "rest"]}})
    pipeline = _pipeline(control)
    assert pipeline._curate(candidate) is not None and pipeline._curate(candidate) is not None
    assert control.calls.count(("curate", "reasoning")) == 1


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
        control = FakeControl({("explain", "reasoning"): {"items": {"operating.subs": {"legitimate": True, "quote": quote}}}})
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


def test_a_region_keeps_its_quarters_name():
    from kpi_extractor.xbrl import XbrlGroup
    base = {"group_key": "geography", "fiscal_year": "2025", "fiscal_period": "Q3", "period_end": "2025-08-29",
            "method": "xbrl", "validation_status": "verified", "value": 1.0, "source_accession": "q3"}
    pipeline = _pipeline(FakeControl({}), [{**base, "kpi_key": k, "kpi_label": l, "locator": {"member": m}} for k, l, m in (
        ("americas", "Americas", "srt:AmericasMember"), ("emea", "EMEA", "us-gaap:EMEAMember"),
        ("apac", "APAC", "srt:AsiaPacificMember"))] + [
        {**base, "fiscal_year": "2024", "fiscal_period": "FY", "period_end": "2024-11-29", "kpi_key": k, "kpi_label": k,
         "locator": {"member": k}} for k in ("unitedstates", "emea", "apac", "other")])
    group = XbrlGroup("geography", "Revenue by Geography", 2,
                      [("unitedstates", "United States", 12529.0), ("emea", "EMEA", 6289.0), ("apac", "APAC", 3360.0),
                       ("other", "Other Americas", 1591.0)], 23769.0, 0.0, "USD", "c",
                      prior={"totalamericas": 12891.0},
                      elements={"totalamericas": "srt:AmericasMember", "emea": "us-gaap:EMEAMember",
                                "apac": "srt:AsiaPacificMember"},
                      alternative=[("totalamericas", "Total Americas", 14120.0), ("emea", "EMEA", 6289.0), ("apac", "APAC", 3360.0)])
    chosen = pipeline._continuing_split(group, date(2025, 11, 28))
    assert chosen.members[0] == ("americas", "Americas", 14120.0) and chosen.prior == {"americas": 12891.0}


def test_a_year_before_any_regional_quarter_follows_the_latest_quarters():
    from kpi_extractor.xbrl import XbrlGroup
    base = {"group_key": "geography", "fiscal_year": "2019", "fiscal_period": "Q1", "period_end": "2019-03-01",
            "method": "xbrl", "validation_status": "verified", "value": 1.0, "source_accession": "q"}
    pipeline = _pipeline(FakeControl({}), [{**base, "kpi_key": k, "kpi_label": l, "locator": {"member": m}} for k, l, m in (
        ("americas", "Americas", "srt:AmericasMember"), ("emea", "EMEA", "us-gaap:EMEAMember"), ("apac", "APAC", "srt:AsiaMember"))])
    group = XbrlGroup("geography", "Revenue by Geography", 2,
                      [("unitedstates", "United States", 4632.0), ("emea", "EMEA", 2550.0), ("otherasia", "Other Asia", 754.0),
                       ("japan", "Japan", 609.0), ("otheramericas", "Other Americas", 484.0)], 9030.0, 0.0, "USD", "c",
                      elements={"america": "us-gaap:AmericasMember", "emea": "us-gaap:EMEAMember", "asia": "us-gaap:AsiaMember"},
                      alternative=[("america", "America", 5117.0), ("emea", "EMEA", 2550.0), ("asia", "Asia", 1363.0)])
    chosen = pipeline._continuing_split(group, date(2018, 11, 30))
    assert [key for key, _l, _v in chosen.members] == ["americas", "emea", "apac"]


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


def test_a_split_of_part_of_a_reported_line_is_proven_by_nearly_filling_it():
    # Adobe's Q4 FY2025 customer groups: $1.72 + $4.25 billion, "Total Customer Group subscription revenue" $5.96 billion,
    # of $5,989 million subscription revenue.
    xbrl = {"group_key": "products", "group_kind": "revenue_breakdown", "method": "xbrl", "validation_status": "verified",
            "fiscal_year": "2025", "fiscal_period": "Q4", "period_end": "2025-11-28", "locator": {}}
    group = {"group_key": "kpi_customer", "group_kind": "revenue_breakdown", "method": "ai", "validation_status": "needs_review",
             "fiscal_year": "2025", "fiscal_period": "Q4", "period_end": "2025-11-28", "unit": "currency",
             "source_accession": "r", "notes": ["total matches no figure the company reported in XBRL"],
             "locator": {"total": 5_960_000_000.0}}
    rows = [{**xbrl, "kpi_key": "subscription", "value": 5_989_000_000.0},
            {**group, "kpi_key": "business", "value": 1_720_000_000.0}, {**group, "kpi_key": "creative", "value": 4_250_000_000.0}]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._prove_flagged([])
    assert pipeline.values[("kpi_customer", "business", "2025", "Q4")]["validation_status"] == "verified"
    short = _pipeline(FakeControl({}), [rows[0], {**rows[1], "value": 1_500_000_000.0}, rows[2]])
    short._prove_flagged([])
    assert short.values[("kpi_customer", "business", "2025", "Q4")]["validation_status"] == "needs_review"


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


def test_a_business_added_the_following_year_does_not_block_the_restatement():
    def row(key, year, period, end, value, prior=None, total=None, accession="a"):
        return {"group_key": "products", "group_kind": "revenue_breakdown", "group_label": "Revenue by Product", "kpi_key": key,
                "kpi_label": key, "fiscal_year": year, "fiscal_period": period, "period_end": end, "method": "xbrl",
                "validation_status": "verified", "value": value, "source_accession": accession,
                "locator": {"total": total, **({"prior": prior} if prior is not None else {})}}
    # Amazon's 2016 10-Q split media from merchandise; its Q3 2017 10-Q restates Q3 2016 by store type, with physical
    # stores (Whole Foods, bought in August 2017) at nothing a year earlier.
    values = [row("online", "2016", "FY", "2016-12-31", 91.0, total=136.0), row("aws", "2016", "FY", "2016-12-31", 45.0, total=136.0),
              row("media", "2016", "Q3", "2016-09-30", 6.0, total=32.0), row("merchandise", "2016", "Q3", "2016-09-30", 26.0, total=32.0),
              row("online", "2017", "Q3", "2017-09-30", 26.0, prior=29.0, total=44.0, accession="b"),
              row("aws", "2017", "Q3", "2017-09-30", 5.0, prior=3.0, total=44.0, accession="b"),
              row("physical", "2017", "Q3", "2017-09-30", 13.0, prior=0.0, total=44.0, accession="b")]
    pipeline = _pipeline(FakeControl({}), values)
    pipeline._adopt_restated_quarters()
    q3 = {k: v["value"] for (g, k, y, p), v in pipeline.values.items() if y == "2016" and p == "Q3" and v["validation_status"] == "verified"}
    assert q3 == {"online": 29.0, "aws": 3.0}


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


def test_a_group_confirmed_on_its_own_stands_and_the_second_reading_reads_only_the_rest():
    html = RELEASE + "<p>Employees at quarter end: 76,000.</p>"
    document = parse_document(html, "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [SPEC.groups[0].model_dump(), {"key": "ops", "label": "Operating", "kind": "metric",
                                "kpis": [{"key": "headcount", "label": "Employees", "unit": "count"}]}]})
    block = next(key for key, item in document.blocks.items() if "Employees" in item.text)
    headcount = {"kpi": "ops.headcount", "block": block, "quote": "Employees at quarter end: 76,000", "value_text": "76,000"}
    flash = {**_answer(document), "values": _answer(document)["values"] + [headcount]}
    control = FakeControl({("locate", "flash"): flash,
                           ("locate", "reasoning"): {"period_end": "2026-06-30", "values": [headcount]}})
    history = _history(document, _answer(document))  # the mix was found here last quarter; headcount is new
    read, _, _ = _pipeline(control, history)._read(lambda subset=None: "prompt", document, spec, EXPECTED)
    assert {item.kpi.key for item in read} == {"n3", "n5", "other", "headcount"}
    assert ("locate", "reasoning") in control.calls and ("review", "reasoning") not in control.calls


def test_code_finds_the_sections_naming_every_kpi():
    html = """<table><tr><td>Data Center</td><td>4,100</td></tr><tr><td>Gaming</td><td>2,900</td></tr></table>
      <table><tr><td>Total assets</td><td>99,000</td></tr></table>"""
    document = parse_document(html, "https://www.sec.gov/x.htm")
    spec = Spec.model_validate({"groups": [{"key": "segments", "label": "Revenue by Segment", "kind": "revenue_breakdown", "kpis": [
        {"key": "datacenter", "label": "Data Center", "unit": "currency"}, {"key": "gaming", "label": "Gaming", "unit": "currency"}]}]})
    pipeline = _pipeline(FakeControl({}))
    assert pipeline._sections_with_every_kpi(document, spec) == {next(iter(document.tables))}
    missing = Spec.model_validate({"groups": [{"key": "ops", "label": "Ops", "kind": "metric", "kpis": [
        {"key": "dap", "label": "Daily Active People", "unit": "count"}]}]})
    assert pipeline._sections_with_every_kpi(document, missing) is None


def test_a_group_that_never_verifies_is_not_read_again():
    spec = Spec.model_validate({"groups": [SPEC.groups[0].model_dump(), {"key": "lines", "label": "Revenue lines", "kind": "metric",
                                "kpis": [{"key": "nii", "label": "Net interest income", "unit": "currency"}]}]})
    rows = [{"group_key": "kpi_lines", "kpi_key": "nii", "fiscal_year": str(2020 + q // 4), "fiscal_period": f"Q{q % 4 + 1}",
             "period_end": f"{2020 + q // 4}-0{q % 4 * 3 + 3}-28", "method": "ai", "validation_status": "needs_review", "value": 1.0}
            for q in range(8)]
    assert [g.key for g in _pipeline(FakeControl({}), rows)._readable(spec).groups] == ["technology"]
    assert [g.key for g in _pipeline(FakeControl({}), rows[:7])._readable(spec).groups] == ["technology", "lines"]


def test_an_older_quarter_read_after_newer_ones_is_compared_with_the_nearest_reading():
    def row(end, value):
        return {"group_key": "kpi_ops", "kpi_key": "users", "fiscal_year": end[:4], "fiscal_period": "Q2", "period_end": end,
                "method": "ai", "validation_status": "verified", "value": value, "source_url": "https://www.sec.gov/x.htm",
                "locator": {"row_label": "Users"}}
    pipeline = _pipeline(FakeControl({}), [row("2026-06-30", 10.0), row("2025-12-31", 9.0)])
    history = pipeline._ai_history(date(2025, 9, 30), "x.htm")
    assert history["ops.users"]["period_end"] == "2025-12-31"  # nothing before: the nearest later quarter
    assert pipeline._ai_history(date(2026, 3, 31), "x.htm")["ops.users"]["period_end"] == "2025-12-31"


def test_quarters_read_side_by_side_are_checked_against_each_other_in_date_order():
    def row(end, value, period):
        return {"group_key": "kpi_ops", "group_kind": "metric", "kpi_key": "users", "unit": "count", "fiscal_year": end[:4],
                "fiscal_period": period, "period_end": end, "method": "ai", "validation_status": "verified", "value": value,
                "notes": []}
    values = [row("2026-03-31", 100.0, "Q1"), row("2026-06-30", 900.0, "Q2"), row("2026-09-30", 105.0, "Q3")]
    pipeline = _pipeline(FakeControl({}), values)
    pipeline._check_read_series({("kpi_ops", "users", "2026", "Q2")})
    assert pipeline.values[("kpi_ops", "users", "2026", "Q2")]["validation_status"] == "needs_review"
    assert pipeline.values[("kpi_ops", "users", "2026", "Q3")]["validation_status"] == "verified"  # not read now


def test_a_kpi_list_is_kept_to_the_cap_by_trimming_metrics_first():
    from kpi_extractor.ai import MAX_LISTED_KPIS, normalize_spec
    breakdown = {"key": "segments", "label": "Revenue by Segment", "kind": "revenue_breakdown",
                 "kpis": [{"key": f"s{i}", "label": f"S{i}", "unit": "currency"} for i in range(10)]}
    metrics = {"key": "operating", "label": "Operating Metrics", "kind": "metric",
               "kpis": [{"key": f"m{i}", "label": f"M{i}", "unit": "count"} for i in range(16)]}
    spec = normalize_spec(Spec.model_validate({"groups": [breakdown, metrics]}))
    assert sum(len(g.kpis) for g in spec.groups) == MAX_LISTED_KPIS
    assert len(spec.groups[0].kpis) == 10  # the breakdown, which is checked by its sum, is kept whole


def test_the_cap_never_drops_a_breakdown():
    from kpi_extractor.ai import normalize_spec
    def breakdown(key, n):
        return {"key": key, "label": key, "kind": "revenue_breakdown",
                "kpis": [{"key": f"{key}{i}", "label": f"{key}{i}", "unit": "currency"} for i in range(n)]}
    metrics = {"key": "operating", "label": "Operating Metrics", "kind": "metric",
               "kpis": [{"key": f"m{i}", "label": f"M{i}", "unit": "count"} for i in range(8)]}
    spec = normalize_spec(Spec.model_validate({"groups": [breakdown("nodes", 12), breakdown("platforms", 7), metrics,
                                                          breakdown("geography", 5)]}))
    assert [g.key for g in spec.groups] == ["nodes", "platforms", "operating", "geography"]
    assert len(spec.groups[2].kpis) == 8  # fewer than the kept minimum: none trimmed


def test_a_jump_alone_never_settles_a_dispute():
    from kpi_extractor.extract import ReadValue
    spec = Spec.model_validate({"groups": [{"key": "ops", "label": "Ops", "kind": "metric",
                                            "kpis": [{"key": "users", "label": "Users", "unit": "count"}]}]})
    group, kpi = spec.groups[0], spec.groups[0].kpis[0]
    pipeline = _pipeline(FakeControl({}), [{"group_key": "kpi_ops", "kpi_key": "users", "fiscal_year": "2026", "fiscal_period": "Q1",
                                            "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": 100.0}])
    disputed = {"ops.users": (ReadValue(group, kpi, 450.0, None, {}, []), ReadValue(group, kpi, 100.0, None, {}, []))}
    agreed: list = []
    pipeline._settle_by_checks(agreed, disputed, date(2026, 6, 30))
    assert agreed == [] and "ops.users" in disputed  # the real move goes to the review, not to last quarter's figure


def test_a_breakdown_reading_that_repeats_an_earlier_period_loses():
    from kpi_extractor.extract import ReadValue
    spec = Spec.model_validate({"groups": [{"key": "seg", "label": "Revenue by Segment", "kind": "revenue_breakdown", "total_kpi": "total",
                                            "kpis": [{"key": "a", "label": "A", "unit": "currency"}, {"key": "b", "label": "B", "unit": "currency"},
                                                     {"key": "total", "label": "Total", "unit": "currency"}]}]})
    group = spec.groups[0]
    a, b, total = group.kpis
    earlier = [{"group_key": "kpi_seg", "kpi_key": "a", "fiscal_year": "2026", "fiscal_period": "Q1", "period_end": "2026-03-31",
                "method": "ai", "validation_status": "verified", "value": 60.0}]
    pipeline = _pipeline(FakeControl({}), earlier)
    agreed = [ReadValue(group, b, 40.0, None, {}, []), ReadValue(group, total, 100.0, None, {}, [], is_total=True)]
    disputed = {"seg.a": (ReadValue(group, a, 60.0, None, {}, []), ReadValue(group, a, 55.0, None, {}, []))}
    pipeline._settle_by_checks(agreed, disputed, date(2026, 6, 30))
    # One adds up only by repeating last quarter's 60, the other does not add up: neither is sound, Pro reviews.
    assert "seg.a" in disputed and agreed[-1].kpi.key != "a"


def test_a_breakdown_read_from_part_of_its_table_is_flagged():
    from kpi_extractor.extract import ReadValue
    spec = Spec.model_validate({"groups": [{"key": "tech", "label": "Revenue by Technology", "kind": "revenue_breakdown", "total_kpi": "total",
                                            "kpis": [{"key": k, "label": k, "unit": "currency"} for k in ("n2", "n3", "n5", "n7", "total")]}]})
    group = spec.groups[0]
    reference = [{"group_key": "kpi_tech", "kpi_key": k, "fiscal_year": "2026", "fiscal_period": "Q1", "period_end": "2026-03-31",
                  "method": "ai", "validation_status": "verified", "value": v, "source_url": "https://www.sec.gov/x.htm",
                  "locator": {"total": 100.0}} for k, v in (("n2", 10.0), ("n3", 30.0), ("n5", 35.0), ("n7", 25.0))]
    pipeline = _pipeline(FakeControl({}), reference)
    kpis = {k.key: k for k in group.kpis}
    read = [ReadValue(group, kpis["n2"], 12.0, None, {}, []), ReadValue(group, kpis["n3"], 30.0, None, {}, []),
            ReadValue(group, kpis["total"], 42.0, None, {}, [], is_total=True)]  # "advanced technologies" taken for the total
    pipeline._flag_partial_tables(read, EXPECTED, parse_document("<p>x</p>", "https://www.sec.gov/x.htm"))
    assert all(item.status == "needs_review" for item in read)


def test_a_release_an_older_reader_left_incomplete_is_read_again():
    from kpi_extractor.pipeline import _NOT_REPORTED, _RELEASE_READER, release_reread_due

    incomplete = {"status": "processed", "notes": ["read by AI", "release reader 0", f"{_NOT_REPORTED}revenue_by_product.net_system_sales"]}
    assert release_reread_due(incomplete) is True
    # Read by the current reader, complete, never stamped (read before stamping began) or not finished: left alone.
    assert release_reread_due({**incomplete, "notes": ["read by AI", _RELEASE_READER, incomplete["notes"][2]]}) is False
    assert release_reread_due({**incomplete, "notes": ["read by AI", "release reader 1", incomplete["notes"][2]]}) is False
    assert release_reread_due({**incomplete, "notes": ["read by AI", "release reader 0"]}) is False
    assert release_reread_due({**incomplete, "notes": ["read by AI", incomplete["notes"][2]]}) is False
    assert release_reread_due({**incomplete, "status": "skipped"}) is False
    assert release_reread_due(None) is False


def test_release_statements_go_to_the_newest_release_presenting_each_quarter():
    from datetime import date as day

    from kpi_extractor.document import Document, TextBlock
    from kpi_extractor.sec import FilingRef
    from test_release_statements import BALANCE

    class Recorder(FakeControl):
        def call(self, operation, **payload):
            self.calls.append((operation, payload))
            return {}

    def release(accession: str, filed: day, period_end: day, text: str) -> tuple[FilingRef, Document]:
        document = Document("https://www.sec.gov/x.htm")
        document.blocks["P0"] = TextBlock("P0", text)
        document.order.append("P0")
        return FilingRef(accession, "6-K", filed, "earnings_release", "https://www.sec.gov/x.htm", period_end), document

    # The newer release presents Jun 2025 to Jun 2026; the older one's five quarters end a quarter earlier.
    older_text = (BALANCE.replace("Jun 29, Sep 28, Dec 31, Mar 29, Jun 28,", "Mar 30, Jun 29, Sep 28, Dec 31, Mar 29,")
                  .replace("2025 2025 2025 2026 2026", "2025 2025 2025 2025 2026"))
    newer, newer_document = release("new", day(2026, 7, 15), day(2026, 6, 28), BALANCE)
    older, older_document = release("old", day(2026, 4, 15), day(2026, 3, 29), older_text)
    control = Recorder({})
    pipeline = _pipeline(control)
    pipeline.offset = 0
    pipeline._statement_owners = {}
    pipeline._document = lambda ref: newer_document if ref.accession == "new" else older_document
    annual = FilingRef("annual", "20-F", day(2026, 2, 25), "periodic_report", "https://www.sec.gov/y.htm")
    pipeline._store_release_statements([annual], [older, newer])
    stored = {payload["accession"]: sorted({line["period_end"] for line in payload["lines"]})
              for operation, payload in control.calls if operation == "store_statements"}
    # Balance sheets carry no income statement, so the older release is opened too; it keeps only the quarter the
    # newer one does not present.
    assert stored == {"new": ["2025-06-29", "2025-09-28", "2025-12-31", "2026-03-29", "2026-06-28"], "old": ["2025-03-30"]}
    # A filer with 10-Qs has its quarters in XBRL: its releases are never read for statements.
    control.calls.clear()
    quarterly = FilingRef("q", "10-Q", day(2026, 5, 1), "periodic_report", "https://www.sec.gov/z.htm")
    pipeline._statement_owners = {}
    pipeline._store_release_statements([annual, quarterly], [older, newer])
    assert control.calls == []


def test_chart_slides_become_revenue_breakdowns_and_unit_metrics_checked_against_the_quarter():
    from datetime import date as day

    from kpi_extractor import pipeline as module
    from kpi_extractor.charts import Word
    from kpi_extractor.sec import FilingRef
    from test_charts import SLIDE

    class Recorder(FakeControl):
        def call(self, operation, **payload):
            self.calls.append((operation, payload))
            return {}

    def stored(kpi_label, fiscal_period, period_end, value, unit="currency", group="kpi_revenue_by_product"):
        return {"group_key": group, "kpi_key": kpi_label.lower().replace(" ", "_"), "kpi_label": kpi_label, "fiscal_year": "2026",
                "fiscal_period": fiscal_period, "period_end": period_end, "unit": unit, "value": value, "currency": "EUR",
                "method": "ai", "validation_status": "verified", "group_label": "x", "source_url": "https://www.sec.gov/x.htm"}

    exhibit = SimpleNamespace(url="https://www.sec.gov/Archives/edgar/data/1/2/deck.htm")
    html = ('<IMG src="deck008.jpg" title="slide8"> <DIV><FONT size="1">Page 8 Net system sales breakdown (quarterly)</FONT></DIV>')
    q2 = FilingRef("q2", "6-K", day(2026, 7, 15), "earnings_release", exhibit.url, day(2026, 6, 28), exhibits=[exhibit])
    q1 = FilingRef("q1", "6-K", day(2026, 4, 15), "earnings_release", exhibit.url, day(2026, 3, 29), exhibits=[])
    control = Recorder({})
    pipeline = _pipeline(control, [
        stored("Net system sales", "Q2", "2026-06-28", 6_564_800_000),
        stored("Net system sales", "Q1", "2026-03-29", 6_279_400_000),
        # Q2's reported units (new and used) are 91, as the bars add up to; Q1's are not known.
        stored("New Lithography Systems Sold", "Q2", "2026-06-28", 85, unit="count", group="kpi_operating"),
        stored("Used Lithography Systems Sold", "Q2", "2026-06-28", 6, unit="count", group="kpi_operating"),
    ])
    pipeline.offset = 0
    pipeline._exhibit = lambda _exhibit: SimpleNamespace(result=lambda: html)
    fetched: list[str] = []
    original = (module._fetch_image, module.ocr_words)
    module._fetch_image = lambda link: fetched.append(link) or b""
    module.ocr_words = lambda _image, *_region: [Word(*word) for word in SLIDE]
    try:
        pipeline._store_chart_breakdowns([q1, q2])
        values = [value for operation, payload in control.calls if operation == "store" for value in payload["values"]]
        # The Q2 slide gives its own quarter and, since the Q1 release had no slide, the quarter before.
        assert {(value["fiscal_period"], value["period_end"]) for value in values} == {("Q2", "2026-06-28"), ("Q1", "2026-03-29")}
        euv = next(value for value in values if value["kpi_label"] == "EUV" and value["fiscal_period"] == "Q2")
        # A pie is a revenue breakdown: 57% of the quarter's net system sales, in its currency.
        assert (euv["group_key"], euv["group_label"], euv["group_kind"], euv["unit"], euv["currency"], euv["method"], euv["kpi_order"]) == (
            "kpi_net_system_sales_by_technology", "Net system sales by technology", "revenue_breakdown", "currency", "EUR", "chart", 0)
        assert euv["value"] == round(6_564_800_000 * 0.57, 2) and euv["locator"]["share"] == 57
        technology = [value for value in values if value["group_key"] == euv["group_key"] and value["fiscal_period"] == "Q2"]
        assert abs(sum(value["value"] for value in technology) - 6_564_800_000) < 1
        korea = next(value for value in values if value["kpi_label"] == "South Korea" and value["fiscal_period"] == "Q1")
        assert korea["group_label"] == "Net system sales by region" and korea["value"] == round(6_279_400_000 * 0.45, 2)
        # Units by technology are operating metrics.
        units = {value["kpi_label"]: value["value"] for value in values
                 if value["group_key"] == "kpi_lithography_units_sold" and value["fiscal_period"] == "Q2"}
        assert units == {"EUV units sold": 16, "ArFi units sold": 23, "ArF Dry units sold": 8, "KrF units sold": 35, "I-line units sold": 9}
        assert {value["group_kind"] for value in values if value["group_key"] == "kpi_lithography_units_sold"} == {"metric"}
        assert fetched == ["https://www.sec.gov/Archives/edgar/data/1/2/deck008.jpg"]
        # Stored quarters are not read again.
        control.calls.clear()
        pipeline._store_chart_breakdowns([q1, q2])
        assert control.calls == [] and len(fetched) == 1
    finally:
        module._fetch_image, module.ocr_words = original


def test_units_that_do_not_add_up_to_the_quarter_are_left_out_and_one_unread_bar_is_filled():
    from datetime import date as day

    from kpi_extractor.charts import ChartUnits
    from kpi_extractor.sec import FilingRef

    def reported(label, value):
        return {"group_key": "kpi_operating", "kpi_key": label.lower().replace(" ", "_"), "kpi_label": label, "fiscal_year": "2025",
                "fiscal_period": "Q3", "period_end": "2025-09-28", "unit": "count", "value": value, "method": "ai",
                "validation_status": "verified", "group_label": "Operating Metrics"}

    pipeline = _pipeline(FakeControl({}), [reported("New Lithography Systems Sold", 66), reported("Used Lithography Systems Sold", 6)])
    ref = FilingRef("q3", "6-K", day(2025, 10, 15), "earnings_release", "https://www.sec.gov/x.htm", day(2025, 9, 28))
    read = {"EUV": 9, "ArFi": 38, "KrF": 11, "I-line": 10}
    # The picture gave no count for the sliver of a bar: it is the quarter's 72 units less the other four.
    link = "https://www.sec.gov/s.jpg"
    pipeline._document = lambda _ref: (_ for _ in ()).throw(ValueError("no statements exhibit"))
    filled = pipeline._unit_records(ChartUnits("2025", "Q3", read, "ArF Dry", frozenset(read)), ref, day(2025, 9, 28), link)
    assert {record["kpi_label"]: record["value"] for record in filled}["ArF Dry units sold"] == 4
    assert "72 units less the other technologies" in next(r for r in filled if r["kpi_label"] == "ArF Dry units sold")["notes"][0]
    # Every bar read, but a count misread: the bars no longer add up to the reported units.
    wrong = pipeline._unit_records(ChartUnits("2025", "Q3", {**read, "ArF Dry": 9}), ref, day(2025, 9, 28), link)
    assert wrong == []
    # A fill would take whatever the total leaves, hiding a misread among the bars that were read (an 8 taken for a
    # 0): so it is made only when two readings agree on each of them.
    once = pipeline._unit_records(ChartUnits("2025", "Q3", read, "ArF Dry", frozenset({"ArFi", "KrF", "I-line"})), ref, day(2025, 9, 28), link)
    assert once == []


def test_a_pie_a_point_off_100_is_stored_only_once_a_second_slide_shows_the_same():
    from datetime import date as day

    from kpi_extractor import pipeline as module
    from kpi_extractor.charts import Word
    from kpi_extractor.sec import FilingRef
    from test_charts import SLIDE

    class Recorder(FakeControl):
        def call(self, operation, **payload):
            self.calls.append((operation, payload))
            return {}

    def stored(fiscal_period, period_end, value):
        return {"group_key": "kpi_revenue_by_product", "kpi_key": "net_system_sales", "kpi_label": "Net system sales",
                "fiscal_year": "2026", "fiscal_period": fiscal_period, "period_end": period_end, "unit": "currency", "value": value,
                "currency": "EUR", "method": "ai", "validation_status": "verified", "group_label": "x",
                "source_url": "https://www.sec.gov/x.htm"}

    # Q2's end-use pie reads 51% + 48%: a slice may have been lost, or a digit misread.
    position = next(index for index, word in enumerate(SLIDE) if word[4] == "Memory")
    short = [list(word) for word in SLIDE]
    share = next(word for word in short if word[4] == "49%" and abs(word[0] - short[position][0]) < 40 and 0 < word[1] - short[position][1] < 40)
    share[4] = "48%"
    exhibit = SimpleNamespace(url="https://www.sec.gov/Archives/edgar/data/1/2/deck.htm")
    html = ('<IMG src="deck008.jpg" title="slide8"> <DIV><FONT size="1">Page 8 Net system sales breakdown (quarterly)</FONT></DIV>')
    q1 = FilingRef("q1", "6-K", day(2026, 4, 15), "earnings_release", exhibit.url, day(2026, 3, 29), exhibits=[])
    q2 = FilingRef("q2", "6-K", day(2026, 7, 15), "earnings_release", exhibit.url, day(2026, 6, 28), exhibits=[exhibit])
    q3 = FilingRef("q3", "6-K", day(2026, 10, 14), "earnings_release", exhibit.url, day(2026, 9, 27), exhibits=[exhibit])

    def run(releases):
        control = Recorder({})
        pipeline = _pipeline(control, [stored("Q2", "2026-06-28", 6_564_800_000), stored("Q1", "2026-03-29", 6_279_400_000)])
        pipeline.offset = 0
        pipeline._exhibit = lambda _exhibit: SimpleNamespace(result=lambda: html)
        original = (module._fetch_image, module.ocr_words)
        module._fetch_image = lambda link: b""
        module.ocr_words = lambda _image, *_region: [Word(*word) for word in short]
        try:
            pipeline._store_chart_breakdowns(releases)
        finally:
            module._fetch_image, module.ocr_words = original
        values = [value for operation, payload in control.calls if operation == "store" for value in payload["values"]]
        return {(value["group_key"], value["fiscal_period"]) for value in values}

    once = run([q1, q2])
    assert ("kpi_net_system_sales_by_end_use", "Q2") not in once and ("kpi_net_system_sales_by_technology", "Q2") in once
    # The next quarter's slide shows Q2 again with the same shares: the rounding is the company's, and it is stored.
    assert ("kpi_net_system_sales_by_end_use", "Q2") in run([q1, q2, q3])


def test_a_pie_missing_from_a_slides_reading_is_taken_from_a_closer_one():
    from types import SimpleNamespace as Pie
    from kpi_extractor.pipeline import fill_pies, missing_pies

    q3 = [Pie(axis="end-use", fiscal_year="2026", fiscal_period="Q3"), Pie(axis="region", fiscal_year="2026", fiscal_period="Q3")]
    q2 = [Pie(axis=axis, fiscal_year="2026", fiscal_period="Q2") for axis in ("technology", "end-use", "region")]
    assert missing_pies([*q3, *q2]) and not missing_pies(q2) and not missing_pies([])
    closer = [Pie(axis="technology", fiscal_year="2026", fiscal_period="Q3"), *q3, *q2]
    assert [(pie.axis, pie.fiscal_period) for pie in fill_pies([*q3, *q2], closer)] == [("technology", "Q3")]


def test_a_release_whose_quote_from_a_bullet_table_was_dropped_is_read_again_once():
    from kpi_extractor.pipeline import _RELEASE_READER, release_reread_due

    dropped = {"status": "skipped", "notes": ["read by AI", "not reported: operating.dap", "flash operating.dap: unknown text block A_T3"]}
    assert release_reread_due(dropped) is True
    assert release_reread_due({**dropped, "status": "processed", "notes": [*dropped["notes"], "release reader 1"]}) is True
    assert release_reread_due({**dropped, "notes": [*dropped["notes"], _RELEASE_READER]}) is False
    assert release_reread_due({**dropped, "status": "failed"}) is False


def test_a_report_without_xbrl_is_judged_once_and_never_counts_past_the_store():
    from kpi_extractor import pipeline
    from kpi_extractor.sec import FilingRef

    stored = []

    class Control:
        def call(self, operation, **payload):
            stored.extend(payload.get("filings") or [])
            return {}

    run = pipeline.SymbolPipeline(Control(), "TSM", 20, False, log=lambda *_: None)
    ref = FilingRef("0001193125-17-122097", "20-F", date(2017, 4, 13), "periodic_report", "https://www.sec.gov/x.htm",
                    period_end=date(2016, 12, 31))
    run.filings[ref.accession] = {"status": "skipped", "attempts": 100, "notes": ["no XBRL"]}
    assert run._pending(ref)  # an unstamped judgement: read again
    run._store_filing(ref, "skipped", notes=["no XBRL", pipeline._XBRL_READER], counted=False)
    assert stored[-1]["attempts"] == 100
    run.filings[ref.accession] = stored[-1]
    assert not run._pending(ref)


def test_a_group_repeating_a_breakdown_still_tagged_in_xbrl_is_never_added():
    from kpi_extractor import ai
    from kpi_extractor.pipeline import apply_maintenance, without_xbrl_repeats

    spec = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating", "kind": "metric",
                                             "kpis": [{"key": "dap", "label": "DAP", "unit": "count"}]}]})
    segments = {"key": "revenue_by_segment", "label": "Revenue by Segment", "kind": "metric",
                "kpis": [{"key": "family_of_apps", "label": "Family of Apps", "unit": "currency"},
                         {"key": "reality_labs", "label": "Reality Labs", "unit": "currency"}]}
    answer = ai.Maintenance.model_validate({"add_groups": [segments], "add_kpis": {}, "retire": []})
    covered = [{"familyofapps", "realitylabs"}]
    assert apply_maintenance(spec, answer, covered) is None
    assert [group.key for group in apply_maintenance(spec, answer, []).groups] == ["operating", "revenue_by_segment"]
    proposed = Spec.model_validate({"groups": [spec.groups[0].model_dump(), segments]})
    assert [group.key for group in without_xbrl_repeats(proposed, covered).groups] == ["operating"]
    assert [group.key for group in without_xbrl_repeats(proposed, covered, keep=proposed).groups] == ["operating", "revenue_by_segment"]



def test_a_group_repeating_xbrl_with_a_subtotal_beside_its_parts_is_still_a_repeat():
    from kpi_extractor.pipeline import repeats_xbrl

    group = ai.GroupSpec.model_validate({"key": "segments", "label": "Revenue by Segment", "kind": "revenue_breakdown", "kpis": [
        {"key": k, "label": label, "unit": "currency"} for k, label in
        [("uhc", "UnitedHealthcare"), ("health", "Optum Health"), ("rx", "Optum Rx"), ("optum", "Total Optum")]]})
    assert repeats_xbrl(group, [{"unitedhealthcare", "optumhealth", "optumrx", "optuminsight", "optumeliminations"}])


def test_every_call_is_flash_and_reasoning_runs_at_the_profiles_effort(monkeypatch):
    from kpi_extractor.control import ai_profile
    monkeypatch.delenv("KPI_AI_PROFILE", raising=False)
    assert ai_profile("flash", True) == ("flash", True, "high")
    assert ai_profile("flash", False) == ("flash", False, None)  # the first reading stays without reasoning
    monkeypatch.setenv("KPI_AI_PROFILE", "flash-max")
    assert ai_profile("flash", True) == ("flash", True, "max")
    monkeypatch.setenv("KPI_AI_PROFILE", "flash-max-all")
    assert ai_profile("flash", False) == ("flash", True, "max")


def test_a_group_dropped_for_repeating_xbrl_has_its_figures_set_aside():
    # Amazon's list carried "Revenue by Product Type" (net product and service sales), which XBRL tags every quarter.
    def row(key, value):
        return {"group_key": "kpi_product_type", "group_kind": "revenue_breakdown", "group_label": "Revenue by Product Type",
                "kpi_key": key, "kpi_label": key, "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30",
                "method": "ai", "validation_status": "verified", "value": value, "locator": {}, "unit": "currency"}
    control = FakeControl({})
    pipeline = _pipeline(control, [row("net_product_sales", 77.6), row("net_service_sales", 123.0)])
    pipeline.profile = SimpleNamespace(name="Amazon", fiscal_year_end="1231", foreign=False)
    pipeline._current_xbrl_members = lambda: [{"netproductsales", "netservicesales"}]
    before = Spec.model_validate({"groups": [
        {"key": "product_type", "label": "Revenue by Product Type", "kind": "revenue_breakdown", "kpis": [
            {"key": "net_product_sales", "label": "Net Product Sales", "unit": "currency"},
            {"key": "net_service_sales", "label": "Net Service Sales", "unit": "currency"}]},
        {"key": "ops", "label": "Operating", "kind": "metric", "kpis": [{"key": "employees", "label": "Employees", "unit": "count"}]}]})
    after = without_xbrl_repeats(before, pipeline._current_xbrl_members())
    assert [group.key for group in after.groups] == ["ops"]
    pipeline._set_aside_dropped(before, after)
    statuses = {v["kpi_key"]: v["validation_status"] for v in pipeline.values.values()}
    assert statuses == {"net_product_sales": "rejected", "net_service_sales": "rejected"}


def test_a_breakdown_part_restated_alone_returns_to_its_filed_figure():
    # TSMC's Q3 2018: nine nodes restated by the Q3 2019 release, 16/20nm (split in two there) not, so the quarter
    # added up to neither total. It goes back to the figures as filed.
    def row(key, value, notes=()):
        return {"group_key": "kpi_technology", "group_kind": "revenue_breakdown", "group_label": "Technology", "kpi_key": key,
                "kpi_label": key, "fiscal_year": "2018", "fiscal_period": "Q3", "period_end": "2018-09-30", "method": "ai",
                "validation_status": "needs_review", "value": value, "locator": {"total": 100.0}, "unit": "currency",
                "notes": list(notes)}
    values = [row("n7", 25.4, ["restated in the next year's release (was 25.3)"]), row("n16", 74.7)]
    pipeline = _pipeline(FakeControl({}), values)
    pipeline._prove_flagged([])
    assert {v["kpi_key"]: v["value"] for v in pipeline.values.values()} == {"n7": 25.3, "n16": 74.7}


def test_a_6k_named_for_its_form_is_never_assumed_empty_like_an_earlier_one():
    # TSMC's 2017 financial statements (no technology table) and its 2018 ones shared a file-name shape.
    pipeline = _pipeline(FakeControl({}))
    pipeline.filings = {"a": {"spec_version": 3, "status": "skipped", "notes": ["read by AI"], "value_count": 0, "period_end": "2017-09-30",
                              "source_url": "https://www.sec.gov/Archives/edgar/data/1046179/000119312517344732/d495393d6k.htm"}}
    document = SimpleNamespace(source_url="https://www.sec.gov/Archives/edgar/data/1046179/000119312518153715/d582139d6k.htm")
    assert pipeline._kind_reports_nothing(document, date(2018, 3, 31), 3) is False


def test_a_table_naming_a_listed_kpi_is_always_shown_to_the_reader():
    # TSMC's Q1 2018 statements: technology under an "Application Type" table, with no row for every listed node.
    from kpi_extractor.document import parse_document
    html = ("<table><tr><td>Application Type</td><td>Three Months Ended March 31, 2018</td></tr>"
            "<tr><td>Communication</td><td>136,894,555</td></tr><tr><td>Resolution</td><td></td></tr>"
            "<tr><td>10-nanometer</td><td>40,853,554</td></tr></table><table><tr><td>Leases</td><td>5</td></tr></table>")
    document = parse_document(html, "https://www.sec.gov/x/d582139d6k.htm")
    spec = Spec.model_validate({"groups": [{"key": "technology", "label": "Technology", "kind": "revenue_breakdown", "kpis": [
        {"key": "n10", "label": "10-nanometer", "unit": "currency"}, {"key": "n3", "label": "3nm", "unit": "currency"}]}]})
    pipeline = _pipeline(FakeControl({}))
    found = pipeline._sections_naming_kpis(document, spec)
    assert len(found) == 1 and "10-nanometer" in document.tables[next(iter(found))].render()


def test_a_reserved_section_fits_before_earlier_choices_fill_the_budget():
    from kpi_extractor.document import parse_document
    html = "".join(f"<p>{'filler ' * 40}{i}</p>" for i in range(30)) + "<table><tr><td>10-nanometer</td><td>40,853,554</td></tr></table>"
    document = parse_document(html, "https://www.sec.gov/x.htm")
    table = next(iter(document.tables))
    everything = set(document.order)
    text = document.render_ids(everything, 2_000, first={table})
    assert "40,853,554" in text and len(text) <= 2_100


def test_a_restated_figure_keeps_the_filed_one_exactly():
    # TSMC's Q3 2018 7nm: 25,336,977 thousand came back as 25,337,000 from a note written to six digits.
    assert f"{25_336_977_000.0:.15g}" == "25336977000"


def test_a_listed_group_the_next_report_tags_goes_from_the_stored_list():
    # Microsoft's FY27 Q1 release was read an hour before its 10-Q: the audit listed its two new segments as KPIs
    # ("Agents and Infra Revenue"), which the 10-Q then tagged as "Agents and Infra". The next run drops the group.
    def row(key, value):
        return {"group_key": "kpi_segment_revenue", "group_kind": "metric", "group_label": "Segment Revenue",
                "kpi_key": key, "kpi_label": key, "fiscal_year": "2027", "fiscal_period": "Q1", "period_end": "2026-09-30",
                "method": "ai", "validation_status": "verified", "value": value, "locator": {}, "unit": "currency"}
    control = FakeControl({})
    pipeline = _pipeline(control, [row("agents_and_infra", 76_795.0), row("devices_and_consumer", 15_484.0)])
    pipeline.profile = SimpleNamespace(name="Microsoft", fiscal_year_end="0630", foreign=False)
    pipeline._current_xbrl_members = lambda: [{"agentsandinfra", "devicesandconsumer"}]
    stored = []
    pipeline._store_spec = lambda spec, names, version, accession: stored.append((spec, version))
    groups = [{"key": "operating", "label": "Operating", "kind": "metric",
               "kpis": [{"key": "microsoft_cloud_revenue", "label": "Microsoft Cloud Revenue", "unit": "currency"}]},
              {"key": "segment_revenue", "label": "Segment Revenue", "kind": "metric", "kpis": [
                  {"key": "agents_and_infra", "label": "Agents and Infra Revenue", "unit": "currency"},
                  {"key": "devices_and_consumer", "label": "Devices and Consumer Revenue", "unit": "currency"}]}]
    spec, version, proposed = pipeline._spec({"groups": groups, "version": 3, "source_accession": "a"}, [])
    assert [group.key for group in spec.groups] == ["operating"] and version == 4 and not proposed
    assert stored and stored[0][1] == 4
    assert {v["validation_status"] for v in pipeline.values.values()} == {"rejected"}
    # An owner's list keeps it.
    spec, version, _ = pipeline._spec({"groups": groups, "version": 3, "owner_edited_at": "2026-10-01"}, [])
    assert len(spec.groups) == 2 and version == 3


def test_a_group_of_lines_beside_their_segment_total_repeats_xbrl():
    from kpi_extractor.pipeline import repeats_xbrl
    group = ai.GroupSpec.model_validate({"key": "si", "label": "SI revenue", "kind": "revenue_breakdown", "kpis": [
        {"key": k, "label": label, "unit": "currency"} for k, label in
        [("ads", "Advertising revenues"), ("infra", "SI solutions & infrastructure revenues"), ("si", "SI revenues")]]})
    products = {"advertising", "sisolutionsinfrastructure", "consumer", "launchservices"}  # labels as _identity_text gives them
    assert repeats_xbrl(group, [products, {"space", "connectivity", "si"}])
    assert not repeats_xbrl(group, [products])



def test_a_relabelled_year_corrects_the_year_before_and_its_operating_income():
    members = {"client": "amd:ClientMember", "datacenter": "amd:DataCenterMember", "gaming": "amd:GamingMember",
               "embedded": "amd:EmbeddedMember", "unallocated": "us-gaap:MaterialReconcilingItemsMember"}

    def row(group, year, period, end, key, value, prior=None):
        return {"group_key": group, "kpi_key": key, "kpi_label": key, "fiscal_year": year, "fiscal_period": period,
                "period_end": end, "method": "xbrl", "validation_status": "verified", "value": value,
                "source_accession": f"{year}{period}", "group_label": group, "group_kind": "revenue_breakdown",
                "locator": {"prior": prior, "member": members[key]}}
    ends = {"2023": ("2023-04-01", "2023-07-01", "2023-09-30"), "2024": ("2024-03-30", "2024-06-29", "2024-09-28"),
            "2025": ("2025-03-29", "2025-06-28", "2025-09-27")}
    # True (datacenter, client, gaming, embedded); AMD tagged Data Center's as Client, Client's as Gaming, Gaming's as
    # Data Center in 2023 and 2024, and its 2024 10-Qs restate 2023 the same way.
    true = {"2023": [(1295, 739, 1757, 1562), (1321, 998, 1581, 1459), (1598, 1453, 1506, 1243)],
            "2024": [(2337, 1368, 922, 846), (2834, 1492, 648, 861), (3549, 1881, 462, 927)]}
    history = []
    for year in ("2023", "2024"):
        for index, period in enumerate(("Q1", "Q2", "Q3")):
            dc, client, gaming, embedded = true[year][index]
            before = true["2023"][index] if year == "2024" else None
            tagged = {"client": (dc, before and before[0]), "gaming": (client, before and before[1]),
                      "datacenter": (gaming, before and before[2]), "embedded": (embedded, before and before[3])}
            history += [row("segments", year, period, ends[year][index], k, v, p) for k, (v, p) in tagged.items()]
    history += [row("segments", "2023", "FY", "2023-12-30", k, v) for k, v in (("client", 4651), ("datacenter", 6496), ("gaming", 6212), ("embedded", 5321))]
    history += [row("segments", "2024", "FY", "2024-12-28", k, v) for k, v in (("client", 7054), ("datacenter", 12579), ("gaming", 2595), ("embedded", 3557))]
    for index, period in enumerate(("Q1", "Q2", "Q3")):
        dc, _client, _gaming, embedded = true["2024"][index]
        history += [row("segments", "2025", period, ends["2025"][index], "datacenter", 1, dc),
                    row("segments", "2025", period, ends["2025"][index], "embedded", 1, embedded)]
    # Operating income in Q1 2024, tagged the same way (Data Center 541, Client 86, Gaming 151), with 2025's comparatives.
    for index, (period, income) in enumerate((("Q1", (541, 86, 151, 342)), ("Q2", (743, 89, 77, 345)), ("Q3", (1041, 276, 12, 372)))):
        dc, client, gaming, embedded = income
        end = ends["2024"][index]
        history += [row("segment_income", "2024", period, end, "client", dc), row("segment_income", "2024", period, end, "gaming", client),
                    row("segment_income", "2024", period, end, "datacenter", gaming), row("segment_income", "2024", period, end, "embedded", embedded),
                    row("segment_income", "2024", period, end, "unallocated", -1000)]
        history += [row("segment_income", "2025", period, ends["2025"][index], "datacenter", 1, dc),
                    row("segment_income", "2025", period, ends["2025"][index], "embedded", 1, embedded)]
    pipeline = _pipeline(FakeControl({}), history)
    pipeline._fix_mislabelled_quarters()

    def quarter(group, year):
        return {k: v["value"] for (g, k, y, p), v in pipeline.values.items()
                if g == group and y == year and p == "Q1" and v["validation_status"] == "verified"}
    assert quarter("segments", "2023") == {"datacenter": 1295, "client": 739, "gaming": 1757, "embedded": 1562}
    assert quarter("segment_income", "2024") == {"datacenter": 541, "client": 86, "gaming": 151, "embedded": 342, "unallocated": -1000}


def test_a_quarter_moved_to_the_year_with_its_release_goes_back_to_q4():
    # Apple's FY2019 Q4 release: its product-type table ("three months ended") was re-filed as the year.
    base = {"group_key": "kpi_product_type", "group_label": "Revenue by Product Type", "group_kind": "revenue_breakdown",
            "fiscal_year": "2019", "period_end": "2019-09-28", "method": "ai", "source_accession": "q4",
            "validation_status": "verified", "locator": {"header": "three months ended @ # #", "total": 64_040e6}}
    rows = [{**base, "kpi_key": "products", "kpi_label": "Products", "fiscal_period": "FY", "value": 51_529e6,
             "notes": ["full-year figures, re-filed from Q4"]},
            {**base, "kpi_key": "products", "kpi_label": "Products", "fiscal_period": "Q4", "value": 51_529e6,
             "validation_status": "rejected", "notes": ["full-year figures filed as Q4; see FY"]}]
    pipeline = _pipeline(FakeControl({}), rows)
    pipeline._refile_annuals()
    assert pipeline.values[("kpi_product_type", "products", "2019", "Q4")]["validation_status"] == "verified"
    assert pipeline.values[("kpi_product_type", "products", "2019", "FY")]["validation_status"] == "rejected"


def test_a_years_remainder_takes_the_name_its_quarters_give_it():
    # AMD's FY2024 10-K tags "All Other" so that nothing tagged closes the gap; its 10-Qs name the line.
    import pandas as pd
    from kpi_extractor.sec import FilingRef
    seg, cons = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_ConsolidationItemsAxis"
    start, end = "2023-12-31", "2024-12-28"

    def fact(value, segment=None, item=None):
        return {"concept": "us-gaap:OperatingIncomeLoss", "numeric_value": value, "period_type": "duration",
                "period_start": start, "period_end": end, seg: segment, cons: item}
    facts = pd.DataFrame([fact(3_482e6, "amd:DataCenterMember", "us-gaap:OperatingSegmentsMember"),
                          fact(2_608e6, "amd:ClientMember", "us-gaap:OperatingSegmentsMember"),
                          fact(-4_190e6, None, "us-gaap:OperatingSegmentsMember"),
                          fact(4_190e6, None, "us-gaap:MaterialReconcilingItemsMember"), fact(1_900e6)])
    quarter = {"group_key": "segment_income", "kpi_key": "other", "kpi_label": "All Other", "fiscal_year": "2024",
               "method": "xbrl", "validation_status": "verified", "value": -1e9}
    pipeline = _pipeline(FakeControl({}), [{**quarter, "kpi_key": k, "kpi_label": l, "fiscal_period": p, "period_end": e}
                                           for p, e in (("Q1", "2024-03-30"), ("Q2", "2024-06-29"), ("Q3", "2024-09-28"))
                                           for k, l in (("datacenter", "Data Center"), ("client", "Client"), ("other", "All Other"))])
    pipeline._member_children = {}
    ref = FilingRef("k", "10-K", date(2025, 2, 5), "periodic_report", "https://www.sec.gov/k.htm")
    records = pipeline._income_records(facts, {}.get, SimpleNamespace(period_end=date(2024, 12, 28), annual=True),
                                       ref, "2024", "FY", [])
    assert [(r["kpi_key"], r["kpi_label"], r["value"]) for r in records][-1] == ("other", "All Other", -4_190e6)
