"""A figure the filing prints rounded in one place and in full in another is read in full."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Located, Locator, Spec
from kpi_extractor.documents.document import Document, Table
from kpi_extractor.readers.extract import read_values
from kpi_extractor.rules.precise_figures import precise_cell, printed_step

Q3 = date(2026, 9, 30)
HIGHLIGHTS = Table("B_T2", "", [["", "THE BANK OF NEW YORK MELLON CORPORATION", "", ""], ["CONSOLIDATED FINANCIAL HIGHLIGHTS", "", "", ""],
                                ["(dollars in millions)", "3Q26", "2Q26", "3Q25"], ["Total revenue", "5,676", "5,698", "5,081"],
                                ["Assets under management (“AUM”) (in trillions) (b)", "$2.2", "$2.2", "$2.1"]])
AUM_TABLE = Table("B_T14", "", [["", "AUM BY PRODUCT TYPE, CHANGES IN AUM AND WEALTH MANAGEMENT CLIENT ASSETS", "", "", "", ""],
                                ["(dollars in billions)", "3Q26", "2Q26", "3Q25", "YTD26", "YTD25"],
                                ["Equity", "$180", "$184", "$180", "", ""], ["Total AUM", "$2,187", "$2,226", "$2,142", "", ""],
                                ["Beginning balance of AUM", "$2,226", "$2,178", "$2,106", "$2,178", "$2,029"],
                                ["Ending balance of AUM", "$2,187", "$2,226", "$2,142", "$2,187", "$2,142"]])
AUM_READING = {"table": "B_T2", "row": 4, "col": 1, "row_label": HIGHLIGHTS.rows[4][0]}


def document(*tables: Table) -> Document:
    doc = Document(source_url="https://www.sec.gov/x.htm")
    for table in tables:
        doc.tables[table.id] = table
        doc.order.append(table.id)
    return doc


def test_a_rounded_figure_is_read_where_the_filing_prints_it_in_full():
    assert printed_step("$2.2") == 0.1 and printed_step("$404,869") == 1
    found = precise_cell(document(HIGHLIGHTS, AUM_TABLE), AUM_READING, 2.2e12, "Assets Under Management (AUM)", Q3, 3, [])
    assert found == ("B_T14", 3, 1, "Total AUM", 1e9)


def test_no_full_figure_when_two_of_the_quarters_figures_round_to_the_reading():
    other = Table("B_T20", "", [["(dollars in billions)", "3Q26"], ["Total AUM", "$2,231"]])
    assert precise_cell(document(HIGHLIGHTS, AUM_TABLE, other), AUM_READING, 2.2e12, "Assets Under Management (AUM)", Q3, 3, []) is None


def test_another_quarters_or_a_year_to_dates_column_is_never_the_full_figure():
    # 2Q26's $2,226 billion also rounds to $2.2 trillion; only the 3Q26 column counts, and YTD26 repeats it.
    assert precise_cell(document(HIGHLIGHTS, AUM_TABLE), AUM_READING, 2.2e12, "Assets Under Management (AUM)",
                        date(2026, 6, 30), 2, []) == ("B_T14", 3, 2, "Total AUM", 1e9)
    no_quarter = Table("B_T14", "", [["(dollars in billions)", "YTD26"], ["Total AUM", "$2,187"]])
    assert precise_cell(document(HIGHLIGHTS, no_quarter), AUM_READING, 2.2e12, "Assets Under Management (AUM)", Q3, 3, []) is None


def test_an_average_is_never_a_period_end_balance():
    summary = Table("A_T1", "", [["", "Second Quarter 2026"], ["Summary income statement", ""], ["Deposits", "405"]])
    summary.context = "(Dollars in billions)"
    average = Table("A_T12", "", [["Average deposits", "Second Quarter 2026"], ["", ""], ["Total deposits", "$404,869"]])
    period_end = Table("B_T3", "", [["Period-end balances (Dollars in millions)", "June 30, 2026"], ["Total deposits", "$405,212"]])
    reading = {"table": "A_T1", "row": 2, "col": 1, "row_label": "Deposits"}
    for table in (summary, average, period_end):
        table.context = table.context or "(Dollars in millions)"
    doc = document(summary, average, period_end)
    assert precise_cell(doc, reading, 405e9, "Average Deposits", date(2026, 6, 30), 2, []) == ("A_T12", 2, 1, "Total deposits", 1e6)
    assert precise_cell(doc, reading, 405e9, "Total Deposits", date(2026, 6, 30), 2, []) == ("B_T3", 1, 1, "Total deposits", 1e6)


def test_a_segments_page_never_gives_the_companys_full_figure():
    segment = Table("A_T21", "INVESTMENT AND WEALTH MANAGEMENT BUSINESS SEGMENT HIGHLIGHTS",
                    [["(dollars in billions)", "3Q26"], ["AUM (in billions)", "$2,187"]])
    assert precise_cell(document(HIGHLIGHTS, segment), AUM_READING, 2.2e12, "Assets Under Management (AUM)", Q3, 3,
                        ["investment and wealth management"]) is None


def test_a_full_figure_reading_is_left_as_it_is():
    reading = {"table": "B_T14", "row": 3, "col": 1, "row_label": "Total AUM"}
    assert precise_cell(document(HIGHLIGHTS, AUM_TABLE), reading, 2.187e12, "Assets Under Management (AUM)", Q3, 3, []) is None
    # The same figure printed in units elsewhere is no fuller than the table's "7" in millions.
    units = Table("A_T0", "(in millions)", [["", "Q3 2026"], ["Used systems sold (units)", "7"]])
    again = Table("A_T5", "", [["", "Q3 2026"], ["Used systems sold (units)", "7,000,000"]])
    assert precise_cell(document(units, again), {"table": "A_T0", "row": 1, "col": 1, "row_label": "Used systems sold (units)"},
                        7e6, "Used systems sold", Q3, 3, []) is None


def test_a_quoted_rounded_figure_is_read_from_the_table_too():
    reading = {"block": "A_P1", "quote": "AUM of $2.2 trillion", "value_text": "$2.2 trillion"}
    assert precise_cell(document(AUM_TABLE), reading, 2.2e12, "Assets Under Management (AUM)", Q3, 3, [])[:3] == ("B_T14", 3, 1)


def test_the_reading_step_reads_the_full_figure_through_the_cells_checks():
    from kpi_extractor.pipeline import SymbolPipeline

    class Control:
        def call(self, operation, **payload):
            return {}

    spec = Spec.model_validate({"groups": [{"key": "kpi_operating", "label": "Operating metrics", "kind": "metric", "kpis": [
        {"key": "aum", "label": "Assets Under Management (AUM)", "unit": "currency"}]}]})
    doc = document(HIGHLIGHTS, AUM_TABLE)
    located = Located(period_end="2026-09-30", values=[Locator(kpi="kpi_operating.aum", table="B_T2", row=4, col=1,
                                                               row_label=HIGHLIGHTS.rows[4][0], scale=1e12)])
    read, _ = read_values(doc, spec, located, "USD")
    assert read[0].value == 2.2e12
    read[0].locator["replayed_from"] = "0001390777-26-000071"  # a replay's reading keeps what read it
    pipeline = SymbolPipeline(Control(), "BK", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile, pipeline.offset = SimpleNamespace(name="BNY", fiscal_year_end="1231", foreign=False), 0
    [item] = pipeline._prefer_precise(read, doc, spec, located, "USD", Q3)
    assert item.value == 2.187e12 and item.locator["table"] == "B_T14"
    assert item.locator["rounded_from"] == {"table": "B_T2", "row": 4, "col": 1, "figure": "$2.2"}
    assert item.notes[-1] == "the full figure, printed rounded elsewhere: $2.2"
    assert item.locator["replayed_from"] == "0001390777-26-000071" and item.locator["row_label"] == "Total AUM"
