"""The list's Other is read from the income statement's Other row, not a detail table's "All other" (Wells Fargo)."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Located, Locator, Spec
from kpi_extractor.documents.document import Document, Table
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.readers.extract import read_values
from kpi_extractor.rules.statement_other import statement_other_cell

Q3 = date(2019, 9, 30)
STATEMENT_TITLE = "Wells Fargo & Company and Subsidiaries CONSOLIDATED STATEMENT OF INCOME"
STATEMENT = Table("A_T120", STATEMENT_TITLE, [
    ["", "Quarter ended September 30,", "", ""], ["(in millions)", "2019", "2018", "% Change"],
    ["Noninterest income", "", "", ""], ["Lease income", "402", "453", "(11)"], ["Other", "1,528", "633", "141"],
    ["Total noninterest income", "10,385", "9,369", "11"], ["Noninterest expense", "", "", ""], ["Other", "5,032", "3,546", "42"]])
DETAIL = Table("A_T142", "Wells Fargo & Company and Subsidiaries NONINTEREST INCOME", [
    ["", "Quarter ended September 30,", "", ""], ["(in millions)", "2019", "2018", "Change"],
    ["Lease income", "402", "453", "(11)"], ["Life insurance investment income", "173", "167", "4"],
    ["All other", "1,355", "466", "191"], ["Total", "$10,385", "9,369", "11"]])
SERIES = {"table": "A_T98", "row": 4, "col": 1, "row_label": "Other", "context": STATEMENT_TITLE, "scale": 1e6}
READING = {"table": "A_T142", "row": 4, "col": 1, "row_label": "All other"}
SPEC = Spec.model_validate({"groups": [{"key": "noninterest_income_by_type", "label": "Noninterest Income by Type",
                                        "kind": "revenue_breakdown", "kpis": [
                                            {"key": "lease", "label": "Lease Income", "unit": "currency"},
                                            {"key": "other", "label": "Other", "unit": "currency"}]}]})


def document(*tables: Table) -> Document:
    doc = Document(source_url="https://www.sec.gov/x.htm")
    for table in tables:
        doc.tables[table.id] = table
        doc.order.append(table.id)
    return doc


def test_the_lists_other_read_from_a_detail_table_is_read_from_the_statements_other_row():
    assert statement_other_cell(document(STATEMENT, DETAIL), READING, SERIES, "Other", Q3) == ("A_T120", 4, 1, "Other")
    # A detail table's "Other (1)" is no statement's row either (Wells Fargo's 2020 Q3, 64 for 220).
    assert statement_other_cell(document(STATEMENT, DETAIL), {**READING, "row_label": "Other (1)"}, SERIES, "Other", Q3) \
        == ("A_T120", 4, 1, "Other")


def test_nothing_moves_unless_the_series_is_the_statements_and_the_reading_is_not():
    doc = document(STATEMENT, DETAIL)
    assert statement_other_cell(doc, {"table": "A_T120", "row": 4, "col": 1, "row_label": "Other"}, SERIES, "Other", Q3) is None
    assert statement_other_cell(doc, READING, {**SERIES, "context": DETAIL.context, "row_label": "All other"}, "Other", Q3) is None
    assert statement_other_cell(doc, READING, None, "Other", Q3) is None
    assert statement_other_cell(doc, {**READING, "row_label": "Lease income"}, SERIES, "Lease Income", Q3) is None
    # The expense section's Other, far from the series' row, is never the line's.
    assert statement_other_cell(doc, READING, {**SERIES, "row": 14}, "Other", Q3) is None
    assert statement_other_cell(document(DETAIL), READING, SERIES, "Other", Q3) is None


def test_a_reading_of_all_other_is_stored_from_the_statement():
    doc = document(STATEMENT, DETAIL)
    pipeline = SymbolPipeline(SimpleNamespace(call=lambda *a, **k: {}), "WFC", quarters=40, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Wells Fargo", fiscal_year_end="1231", foreign=False, sic="6021")
    pipeline.values = {("other", "2019Q2"): {
        "symbol": "WFC", "group_key": "kpi_noninterest_income_by_type", "kpi_key": "other", "fiscal_year": "2019",
        "fiscal_period": "Q2", "period_end": "2019-06-30", "method": "ai", "validation_status": "verified", "value": 744e6,
        "locator": SERIES, "source_url": "https://www.sec.gov/y.htm"}}
    located = Located(period_end="2019-09-30", tables=[], values=[
        Locator(kpi="noninterest_income_by_type.other", table="A_T142", row=4, col=1, row_label="All other", scale=1e6)])
    read, _problems = read_values(doc, SPEC, located, "USD")
    assert read[0].value == 1355e6
    kept = pipeline._prefer_precise(read, doc, SPEC, located, "USD", Q3)
    assert kept[0].value == 1528e6 and kept[0].locator["table"] == "A_T120" and kept[0].locator["row"] == 4
    assert kept[0].locator["moved_from"]["row_label"] == "All other"
    assert any(note.startswith("read from the income statement's Other row") for note in kept[0].notes)
