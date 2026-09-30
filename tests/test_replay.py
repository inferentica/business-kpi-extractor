import json
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.document import parse_document
from kpi_extractor.extract import read_values
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.replay import replay, reported_period

URL = "https://www.sec.gov/Archives/q{n}report.htm"


def report(q, n2, n3, n5, others, members, extra_row=""):
    total = n2 + n3 + n5 + others + (7 if extra_row else 0)
    return f"""
<p>Quarterly report for the three months ended {q}, 2026 (Amounts in Thousands of New Taiwan Dollars)</p>
<table>
  <tr><td></td><td>Three Months Ended</td></tr>
  <tr><td>Resolution</td><td>2026</td><td>2025</td></tr>
  {extra_row}
  <tr><td>2-nanometer</td><td>{n2}</td><td>1</td></tr>
  <tr><td>3-nanometer</td><td>{n3}</td><td>2</td></tr>
  <tr><td>5-nanometer</td><td>{n5}</td><td>3</td></tr>
  <tr><td>Others</td><td>{others}</td><td>4</td></tr>
  <tr><td>Wafer revenue</td><td>{total}</td><td>10</td></tr>
</table>
<p>Paid memberships reached {members} million in the quarter, up 12% from a year ago.</p>
"""


SPEC = Spec.model_validate({"groups": [
    {"key": "technology", "label": "Revenue by Technology", "kind": "revenue_breakdown",
     "kpis": [{"key": "n3", "label": "3nm", "unit": "currency"}]},
    {"key": "operating", "label": "Operating Metrics", "kind": "metric",
     "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"}]},
]})
Q1, Q2 = date(2026, 3, 31), date(2026, 6, 30)


def _first_quarter(document):
    table = next(iter(document.tables))
    block = next(key for key, item in document.blocks.items() if "Paid memberships" in item.text)
    return ai.Located.model_validate({"period_end": "2026-03-31", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 2, "last_row": 5, "total_row": 6}],
        "values": [{"kpi": "operating.members", "block": block, "quote": "Paid memberships reached 300 million",
                    "value_text": "300 million"}]})


def _history(document):
    items, problems = read_values(document, SPEC, _first_quarter(document), "TWD")
    assert problems == []
    return [{"group_key": f"kpi_{item.group.key}", "kpi_key": item.kpi.key, "fiscal_year": "2026", "fiscal_period": "Q1",
             "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value,
             "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
             "source_url": URL.format(n=1), "source_accession": "q1"} for item in items if not item.is_total]


class NoAi:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def ai(self, symbol, purpose, system, user, thinking, model="flash"):
        self.calls.append((purpose, model))
        return json.dumps(self.answers[(purpose, model)])

    def call(self, operation, **payload):
        return {}


def _pipeline(control, values, notes=("read by AI",)):
    pipeline = SymbolPipeline(control, "TSM", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="TSMC", fiscal_year_end="1231", foreign=True)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "processed", "notes": list(notes)}}
    return pipeline


def test_next_quarter_is_read_again_by_code_with_new_rows_and_new_numbers():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310, "<tr><td>A16</td><td>7</td><td>-</td></tr>"),
                            URL.format(n=2))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first)}
    located = replay(second, last, Q2)
    assert located is not None and located.period_end == "2026-06-30"
    values, problems = read_values(second, SPEC, located, "TWD")
    assert problems == []
    read = {item.kpi.key: item.value for item in values if not item.is_total}
    assert read["members"] == 310_000_000
    assert read["r3nanometer"] == 25_000 and "a16" not in read  # the new row sits above the old first row


def test_a_replayed_quarter_needs_no_ai_call():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    control = NoAi()
    pipeline = _pipeline(control, _history(first))
    read, problems, _ = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert control.calls == [] and problems == [] and pipeline.result.replayed_reads == 1
    assert {item.kpi.key: item.value for item in read if not item.is_total}["r2nanometer"] == 11_000


def test_replay_is_refused_when_a_number_repeats_last_year_or_the_list_changed():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    flash = {"period_end": "2026-06-30", "values": []}
    answers = {("locate", "flash"): flash, ("locate", "pro"): flash}
    history = _history(first)
    same = parse_document(report("June 30", 10, 20, 30, 40, 300), URL.format(n=2))  # last quarter's numbers again
    control = NoAi(answers)
    _pipeline(control, history)._read(lambda: "prompt", same, SPEC, Q2, version=1)
    assert ("locate", "flash") in control.calls
    control = NoAi(answers)
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    _pipeline(control, history)._read(lambda: "prompt", second, SPEC, Q2, version=2)  # a new KPI list: the AI reads
    assert ("locate", "flash") in control.calls


def test_kpis_missing_last_quarter_are_looked_for_by_the_ai_every_other_quarter():
    spec = Spec.model_validate({"groups": [*SPEC.model_dump()["groups"][:1], {
        "key": "operating", "label": "Operating Metrics", "kind": "metric",
        "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"},
                 {"key": "stores", "label": "Stores", "unit": "count"}]}]})
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    flash = {"period_end": "2026-06-30", "values": []}
    control = NoAi({("locate", "flash"): flash, ("locate", "pro"): flash})
    _pipeline(control, _history(first), notes=("read by AI", "not reported: operating.stores"))._read(
        lambda: "prompt", second, spec, Q2, version=1)
    assert control.calls == []
    control = NoAi({("locate", "flash"): flash, ("locate", "pro"): flash})
    _pipeline(control, _history(first), notes=("read by replay", "not reported: operating.stores"))._read(
        lambda: "prompt", second, spec, Q2, version=1)
    assert ("locate", "flash") in control.calls


def test_a_prior_year_column_is_never_replayed():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": {**v["locator"], "col": 2} for v in _history(first)
            if "quote" not in v["locator"]}
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    assert replay(second, last, Q2) is None


def test_the_stated_quarter_end_wins_over_the_nominal_one():
    document = parse_document("<p>Results for the quarter ended June 27, 2026. As of June 27, 2026.</p>", URL.format(n=2))
    assert reported_period(document, Q2) == date(2026, 6, 27)


def test_a_kind_of_document_both_models_found_empty_is_read_by_flash_alone():
    document = parse_document("<p>Monthly revenue report for June.</p>", URL.format(n=2))
    empty = {"period_end": "2026-06-30", "values": []}
    control = NoAi({("locate", "flash"): empty})
    pipeline = _pipeline(control, [])
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "skipped", "value_count": 0,
                               "notes": ["read by AI"], "source_url": URL.format(n=1), "period_end": "2026-03-31"}}
    read, _, _ = pipeline._read(lambda: "prompt", document, SPEC, Q2, version=1)
    assert read == [] and control.calls == [("locate", "flash")]


def test_a_partial_flash_reading_of_an_empty_kind_still_goes_to_pro():
    document = parse_document("<p>Quarterly report.</p><table><tr><td>Wafer</td><td>5</td></tr></table>", URL.format(n=2))
    table = next(iter(document.tables))
    partial = {"period_end": "2026-06-30", "tables": [
        {"group": "technology", "table": table, "col": 1, "first_row": 0, "last_row": 0}]}
    control = NoAi({("locate", "flash"): partial, ("locate", "pro"): {"period_end": "2026-06-30"}})
    pipeline = _pipeline(control, [])
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "skipped", "value_count": 0,
                               "notes": ["read by AI"], "source_url": URL.format(n=1), "period_end": "2026-03-31"}}
    pipeline._read(lambda: "prompt", document, SPEC, Q2, version=1)
    assert ("locate", "pro") in control.calls


def test_a_six_month_column_is_never_replayed_as_the_quarter():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    last = {f"{v['group_key'][4:]}.{v['kpi_key']}": v["locator"] for v in _history(first)}
    six = report("June 30", 21, 45, 61, 82, 310).replace("Three Months Ended", "Six Months Ended")
    assert replay(parse_document(six, URL.format(n=2)), last, Q2) is None


def test_a_quote_about_another_node_is_never_taken_for_this_one():
    document = parse_document("<p>In the quarter, 3-nanometer shipments accounted for 28% of wafer revenue.</p>"
                              "<p>5-nanometer process technology accounted for 35%.</p>", URL.format(n=1))
    block = next(key for key, item in document.blocks.items() if "3-nanometer" in item.text)
    last = {"technology.n3": {"block": block, "quote": "3-nanometer shipments accounted for 28%", "value_text": "28%"}}
    changed = parse_document("<p>In the quarter, 5-nanometer shipments accounted for 35% of wafer revenue.</p>",
                             URL.format(n=2))
    assert replay(changed, last, Q2) is None
