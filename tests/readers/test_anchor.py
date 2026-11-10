"""The anchor-phrase rule, the first learned rule: a quoted figure found again by the words before it, tried beside the
AI in shadow, reading on its own once live, spot-checked, and re-read when demoted."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor import ai
from kpi_extractor.ai import Spec
from kpi_extractor.documents.document import Document, TextBlock, parse_document
from kpi_extractor.ops.learned import spot_checked
from kpi_extractor.ops.reread import picked_filings
from kpi_extractor.readers.anchor import anchor_locator
from kpi_extractor.readers.extract import read_values
from kpi_extractor.readers.replay import _same_place
from tests.readers.test_replay import NoAi, _pipeline

URL = "https://www.sec.gov/Archives/q{n}report.htm"
Q2 = date(2026, 6, 30)
SPEC = Spec.model_validate({"groups": [{"key": "operating", "label": "Operating Metrics", "kind": "metric",
                                        "kpis": [{"key": "members", "label": "Paid Memberships", "unit": "count"}]}]})
FIRST = "<p>Results for the quarter ended March 31, 2026. Paid memberships reached 300 million in the quarter, up 12% from a year ago.</p>"
# Worded anew: replay cannot find the sentence, but the three words before the figure stand.
SECOND = "<p>Results for the quarter ended June 30, 2026. This quarter our paid memberships reached 310 million worldwide.</p>"


def text(document_html: str, n: int):
    return parse_document(document_html, URL.format(n=n))


def history():
    first = text(FIRST, 1)
    block = next(iter(first.blocks))
    located = ai.Located.model_validate({"period_end": "2026-03-31", "values": [
        {"kpi": "operating.members", "block": block, "quote": "Paid memberships reached 300 million in the quarter, up 12%",
         "value_text": "300 million"}]})
    items, problems = read_values(first, SPEC, located, "USD")
    assert problems == []
    return [{"group_key": "kpi_operating", "kpi_key": "members", "fiscal_year": "2026", "fiscal_period": "Q1",
             "period_end": "2026-03-31", "method": "ai", "validation_status": "verified", "value": item.value,
             "locator": item.locator, "group_label": item.group.label, "kpi_label": item.kpi.label,
             "source_url": URL.format(n=1), "source_accession": "q1"} for item in items]


def flash_answer(second):
    block = next(iter(second.blocks))
    return {"period_end": "2026-06-30", "values": [{"kpi": "operating.members", "block": block,
                                                    "quote": "our paid memberships reached 310 million", "value_text": "310 million"}]}


def test_the_words_before_a_figure_find_it_again_once_and_only_once():
    document = Document(source_url="x")
    document.blocks["B1"] = TextBlock(id="B1", text="Current Remaining Performance Obligations (“cRPO”) were 65% of total RPO.")
    document.order.append("B1")
    last = {"quote": "Current Remaining Performance Obligations (“cRPO”) were 67 percent", "value_text": "67 percent"}
    locator, anchor = anchor_locator(document, "operating.crpo", last)
    assert (locator.quote, locator.value_text, anchor) == ("Obligations (“cRPO”) were 65%", "65%", "obligations crpo were")
    # The same phrase twice in the document (this year's and last year's): no reading.
    document.blocks["B2"] = TextBlock(id="B2", text="A year ago, cRPO were 70% of total RPO.")
    document.order.append("B2")
    assert anchor_locator(document, "operating.crpo", {**last, "quote": "cRPO were 67 percent"}) == "anchor phrase leads to several figures"
    # Its reading is where last quarter's was when the words before the figure are the same.
    assert _same_place({"anchor": "crpo were", "quote": "cRPO were 65%"}, {"quote": "cRPO were 67 percent", "value_text": "67 percent"})


def test_in_shadow_the_ai_reads_and_the_rules_figure_is_evidence():
    second = text(SECOND, 2)
    control = NoAi({("locate", "flash"): flash_answer(second), ("locate", "reasoning"): flash_answer(second)})
    pipeline = _pipeline(control, history(), notes=("read by AI",))
    pipeline.learned_rules = {"anchor-phrase": "shadow"}
    pipeline._local.accession, pipeline._local.rule_candidates = "ACC-1", {}
    read, _, _ = pipeline._read(lambda *_, **__: "prompt", second, SPEC, Q2, version=1)
    pipeline._settle_rule_evidence("ACC-1", read)
    assert ("locate", "flash") in control.calls  # the AI still reads
    [row] = pipeline.result.rule_evidence
    assert (row["kind"], row["mode"], row["agreed"], row["ruleValue"]) == ("anchor-phrase", "shadow", True, 310e6)
    assert row["ruleLocator"]["anchor"] == "paid memberships reached"


def test_live_the_rule_reads_with_no_ai_call_but_one_filing_in_ten_is_spot_checked():
    assert not spot_checked("ACC-1") and spot_checked("ACC-15")
    second = text(SECOND, 2)
    control = NoAi()  # any AI call would fail: none is expected
    pipeline = _pipeline(control, history(), notes=("read by AI",))
    pipeline.learned_rules = {"anchor-phrase": "live"}
    pipeline._local.accession, pipeline._local.rule_candidates = "ACC-1", {}
    read, _, _ = pipeline._read(lambda *_, **__: "prompt", second, SPEC, Q2, version=1)
    assert control.calls == [] and read[0].value == 310e6 and read[0].locator["rule"] == "anchor-phrase"
    assert pipeline.result.replay_outcomes == {"read by learned rule anchor-phrase": 1}
    # A spot-checked filing: the AI reads as well, and the rule's figure is evidence ("spot-check").
    control = NoAi({("locate", "flash"): flash_answer(second), ("locate", "reasoning"): flash_answer(second)})
    pipeline = _pipeline(control, history(), notes=("read by AI",))
    pipeline.learned_rules = {"anchor-phrase": "live"}
    pipeline._local.accession, pipeline._local.rule_candidates = "ACC-15", {}
    read, _, _ = pipeline._read(lambda *_, **__: "prompt", second, SPEC, Q2, version=1)
    pipeline._settle_rule_evidence("ACC-15", read)
    assert ("locate", "flash") in control.calls and pipeline.result.rule_evidence[0]["mode"] == "spot-check"


def test_a_demoted_rule_is_not_used_and_its_figures_are_re_read():
    second = text(SECOND, 2)
    control = NoAi({("locate", "flash"): flash_answer(second), ("locate", "reasoning"): flash_answer(second)})
    pipeline = _pipeline(control, history(), notes=("read by AI",))
    pipeline.learned_rules = {"anchor-phrase": "demoted"}
    pipeline._local.accession, pipeline._local.rule_candidates = "ACC-1", {}
    read, _, _ = pipeline._read(lambda *_, **__: "prompt", second, SPEC, Q2, version=1)
    pipeline._settle_rule_evidence("ACC-1", read)
    assert pipeline.result.rule_evidence == [] and not read[0].locator.get("rule")
    values = {1: {"source_accession": "BY-RULE", "validation_status": "verified", "locator": {"rule": "anchor-phrase"}},
              2: {"source_accession": "BY-AI", "validation_status": "verified", "locator": {"ai_call": "c1"}}}
    assert picked_filings("rule-anchor-phrase", values) == {"BY-RULE"}
