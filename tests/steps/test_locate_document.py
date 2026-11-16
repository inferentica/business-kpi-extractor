"""Locate reads the parts of a long document where each KPI sat last, and the whole of it once that missed one."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.documents.document import Document, Table, TextBlock
from kpi_extractor.pipeline import SymbolPipeline

SPEC = Spec.model_validate({"groups": [{"key": "fees", "label": "Fees", "kind": "revenue_breakdown", "kpis": [
    {"key": "advisory", "label": "Advisory", "unit": "currency"}, {"key": "underwriting", "label": "Underwriting", "unit": "currency"}]}]})


class Control:
    def call(self, operation, **payload):
        return {}


def supplement(filler_tables: int = 60) -> Document:
    doc = Document(source_url="https://www.sec.gov/s.htm")
    items = [TextBlock(id="A_P0", text="Fourth quarter 2026 results")]
    items += [Table(id=f"A_T{i}", context="", rows=[["", "4Q26"], [f"Other line {i}", "1,000"], [f"Another {i}", "2,000"]] * 12)
              for i in range(filler_tables)]
    items.append(Table(id=f"A_T{filler_tables}", context="", rows=[["", "4Q26"], ["Advisory (a)", "771"], ["Underwriting", "692"]]))
    for item in items:
        (doc.tables if isinstance(item, Table) else doc.blocks)[item.id] = item
        doc.order.append(item.id)
    return doc


def run(history=()):
    pipeline = SymbolPipeline(Control(), "GS", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Goldman", fiscal_year_end="1231", foreign=False)
    pipeline.store.load(values=[{"group_key": "kpi_fees", "kpi_key": key, "fiscal_year": "2026", "fiscal_period": "Q3",
                                 "period_end": "2026-09-30", "method": "ai", "validation_status": "verified", "value": 1.0,
                                 "locator": {"table": "A_T60", "row": 1, "col": 1, "row_label": label}}
                                for key, label in history])
    return pipeline


def test_a_long_document_is_narrowed_to_the_tables_naming_each_kpi_and_its_opening():
    doc = supplement()
    pipeline = run([("advisory", "Advisory"), ("underwriting", "Underwriting")])
    text = pipeline._locate_document(doc, SPEC)
    assert "[A_T60]" in text and "[A_P0]" in text and "Other line 5" not in text
    assert len(text) < len(doc.render()) * 0.6
    # The same text for the second reading, so the two share the prompt cache.
    assert pipeline._locate_document(doc, SPEC) == text


def test_a_short_document_a_kpi_never_read_or_a_missed_kpi_sends_the_whole_one():
    short = supplement(filler_tables=2)
    assert run([("advisory", "Advisory"), ("underwriting", "Underwriting")])._last_places(short, SPEC) is None
    renamed = supplement()
    renamed.tables["A_T60"].rows[2][0] = "Capital markets"
    assert run([("advisory", "Advisory")])._last_places(renamed, SPEC) is None  # underwriting: never read, named nowhere
    pipeline = run([("advisory", "Advisory"), ("underwriting", "Underwriting")])
    doc = supplement()
    narrowed = pipeline._locate_document(doc, SPEC)
    pipeline._whole_documents.add(str(id(doc)))  # what _read_ai does once the narrowed reading missed a KPI
    assert pipeline._locate_document(doc, SPEC) != narrowed and "Other line 5" in pipeline._locate_document(doc, SPEC)


def test_a_metric_a_narrowed_reading_missed_is_read_again_from_the_whole_document():
    # The re-check's probe: underwriting moved more than a table from its last place, Flash's narrowed reading missed it,
    # and advisory confirmed on its own: the reading stood and underwriting was noted not reported. Now its group is read
    # again by the second reader, from the whole document.
    from kpi_extractor.ai import Located
    from kpi_extractor.readers.extract import ReadValue
    doc = supplement()
    pipeline = run([("advisory", "Advisory"), ("underwriting", "Underwriting")])
    group = SPEC.groups[0]
    advisory = ReadValue(group, group.kpis[0], 771e6, "USD", {"table": "A_T60", "row": 1, "col": 1})
    underwriting = ReadValue(group, group.kpis[1], 692e6, "USD", {"table": "A_T60", "row": 2, "col": 1})
    sent = []

    def locate(text, document, spec, currency, model, thinking_first):
        sent.append(pipeline._locate_document(document, spec))
        return Located(period_end="2026-12-31"), [advisory, underwriting] if thinking_first else [advisory], []

    pipeline._locate = locate
    pipeline._why_not_alone = lambda *_: None  # the narrowed reading passes every check
    pipeline._confirmed_groups = lambda *_: {"fees"}
    pipeline._drop_wrong_columns = lambda read, problems, *_: (read, problems)
    pipeline._prefer_precise = lambda read, *_: read
    pipeline._settle_by_checks = lambda agreed, disputed, _expected: (agreed.extend(b for _a, b in disputed.values()), disputed.clear())
    read, _problems, _located = pipeline._read_ai(lambda *_a, **_k: pipeline._locate_document(doc, SPEC), doc, SPEC,
                                                  date(2026, 12, 31), None, "USD")
    assert {item.kpi.key for item in read} == {"advisory", "underwriting"}
    assert "Other line 5" not in sent[0] and "Other line 5" in sent[1]  # the second reading had the whole document
    assert pipeline.result.second_read_reasons == {"narrowed_missed": 1}
