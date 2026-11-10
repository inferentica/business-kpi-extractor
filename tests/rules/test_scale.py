"""One scale resolver per kind of reading: it keeps the old order of sources and says which one decided, in its words."""
from types import SimpleNamespace

from kpi_extractor.documents.document import Document, Table, TextBlock
from kpi_extractor.rules.scale import (SCALE_NOT_STATED, ScaleFrom, cell_amount_scale, cell_count_scale, number_scale,
                                       passage_amount_scale, passage_count_scale, reading_scale)


def reader(scale=None, scale_text=None):
    return SimpleNamespace(scale=scale, scale_text=scale_text)


def document(*items):
    doc = Document(source_url="https://www.sec.gov/x.htm")
    for item in items:
        (doc.tables if isinstance(item, Table) else doc.blocks)[item.id] = item
        doc.order.append(item.id)
    return doc


def test_an_amount_takes_its_row_then_its_table_then_the_document():
    # The header is the first four rows, so the row with its own scale comes after them, as in JPM's supplement.
    table = Table(id="T1", context="($ in millions)", rows=[["", "Q3"], ["Net revenue", "55"], ["Expense", "30"], ["Income", "25"],
                                                             ["Card sales volume (in billions)", "1,234"]])
    doc = document(table)
    own, _ = cell_amount_scale(doc, table, 4, 1, "Card sales volume", reader(), 1234)
    assert own == ScaleFrom(1e9, "row", "(in billions)")
    caption, _ = cell_amount_scale(doc, table, 1, 1, "Net revenue", reader(), 55)
    assert (caption.factor, caption.source) == (1e6, "table") and "in millions" in caption.words
    bare = Table(id="T2", context="", rows=[["", "Q3"], ["Net revenue", "55"]])
    heading = TextBlock(id="B1", text="All amounts in thousands of dollars unless noted")
    from_document, _ = cell_amount_scale(document(bare, heading), bare, 1, 1, "Net revenue", reader(), 55)
    assert (from_document.factor, from_document.source) == (1e3, "document")


def test_a_price_keeps_its_units_and_a_readers_scale_alone_is_held():
    table = Table(id="T1", context="($ in millions)", rows=[["", "Q3"], ["Revenue per package", "7.31"]])
    per_unit, notes = cell_amount_scale(document(table), table, 1, 1, "Revenue per package", reader(), 7.31)
    assert (per_unit.factor, per_unit.source, notes) == (1.0, "per-unit", [])
    bare = Table(id="T2", context="", rows=[["", "Q3"], ["Net revenue", "55"]])
    held, notes = cell_amount_scale(document(bare), bare, 1, 1, "Net revenue", reader(1e6, "in millions"), 55)
    assert (held.factor, held.source, notes) == (1e6, "reader", [SCALE_NOT_STATED])
    assert cell_amount_scale(document(bare), bare, 1, 1, "Net revenue", reader(), 55) == (None, [])


def test_a_count_takes_no_table_caption():
    table = Table(id="T1", context="(in millions, except employee data)", rows=[["", "Q3"], ["Employees", "12,400"],
                                                                                 ["Paid memberships (in millions)", "301"]])
    doc = document(table)
    assert cell_count_scale(doc, table, 1, 1, reader()) == (ScaleFrom(1.0, "units"), [])
    assert cell_count_scale(doc, table, 2, 1, reader())[0] == ScaleFrom(1e6, "row", "(in millions)")


def test_a_passage_takes_the_last_heading_before_its_quote():
    block = TextBlock(id="B1", text="($ in millions) Net revenue was 55. ($ in billions) AUM reached 908.")
    doc = document(block)
    found, _ = passage_amount_scale(doc, block, block.text.find("AUM reached"), "Assets under management", reader(), 908)
    assert (found.factor, found.source, found.words) == (1e9, "passage", "($ in billions)")
    assert passage_count_scale("Starlink Subscribers (#, in millions) ") == ScaleFrom(1e6, "row", "(#, in millions)")
    assert passage_count_scale("Customers ") == ScaleFrom(1.0, "units")


def test_a_whole_table_needs_a_stated_or_quoted_scale():
    table = Table(id="T1", context="(in thousands)", rows=[["", "Q3"], ["Americas", "1,200"], ["Europe", "800"]])
    found, problem = reading_scale(document(table), table, 1, 1, reader())
    assert (found.factor, found.source, problem) == (1e3, "table", None)
    bare = Table(id="T2", context="", rows=[["", "Q3"], ["Americas", "1,200"]])
    none, problem = reading_scale(document(bare), bare, 1, 1, reader(1e6, "in millions"))
    assert none is None and "not stated in the table" in problem


def test_a_figure_that_states_its_own_scale_says_so():
    assert number_scale("billion") == ScaleFrom(1e9, "number", "billion")
    assert ScaleFrom(1e6, "table", "($ in millions)").record() == {"factor": 1e6, "source": "table", "words": "($ in millions)"}


def test_a_column_headed_as_a_quarter_states_its_period():
    from kpi_extractor.rules.period import states_quarter
    assert all(states_quarter(header) for header in ("Three Months Ended December 31, 2025", "Q4 2025", "4Q25", "13 weeks ended"))
    assert not any(states_quarter(header) for header in ("December 31, 2025", "Twelve Months Ended", "Fiscal 2025"))
