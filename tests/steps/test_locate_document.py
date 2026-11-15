"""Locate reads the parts of a long document where each KPI sat last, and the whole of it once that missed one."""
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


def test_a_kpi_looked_for_before_and_not_reported_no_longer_keeps_the_whole_document():
    doc = supplement()
    spec = Spec.model_validate({"groups": [{"key": "fees", "label": "Fees", "kind": "revenue_breakdown", "kpis": [
        {"key": "advisory", "label": "Advisory", "unit": "currency"}, {"key": "underwriting", "label": "Underwriting", "unit": "currency"},
        {"key": "trading", "label": "Prime brokerage", "unit": "currency"}]}]})
    pipeline = run([("advisory", "Advisory"), ("underwriting", "Underwriting")])
    assert pipeline._last_places(doc, spec) is None  # prime brokerage: never looked for, named nowhere
    pipeline.store.load(values=list(pipeline.values.values()), filings=[{"accession": "Q3", "status": "processed",
                                                                         "notes": ["not reported: fees.trading"]}])
    assert "A_T60" in pipeline._last_places(doc, spec)
