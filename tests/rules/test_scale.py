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


def test_a_price_whose_row_states_its_unit_keeps_it_under_a_document_in_millions():
    # Exxon's benchmarks ("Brent ($/b) 74.35") in a release whose earnings tables are "in millions": a price, never scaled.
    table = Table(id="T1", context="", rows=[["", "2Q18"], ["Brent ($/b)", "74.35"], ["Henry Hub ($/mbtu)", "2.80"]])
    heading = TextBlock(id="B1", text="Earnings (millions of dollars)")
    for row, value in ((1, 74.35), (2, 2.80)):
        own, notes = cell_amount_scale(document(table, heading), table, row, 1, "Brent", reader(), value)
        assert (own.factor, own.source, notes) == (1.0, "row", [])


def test_a_row_naming_its_currency_alone_is_in_units_under_a_table_in_millions():
    # American Express's "Average proprietary basic Card Member spending (dollars)" $6,110: never $6.1 billion.
    table = Table(id="T1", context="(Millions, except percentages and where indicated)",
                  rows=[["", "Q3'24"], ["Average proprietary basic Card Member spending (dollars)", "6,110"],
                        ["Network volumes (billions)", "464.0"], ["Total revenues net of interest expense", "16,635"]])
    doc = document(table)
    own, _notes = cell_amount_scale(doc, table, 1, 1, "Average Proprietary Basic Card Member Spending", reader(), 6110)
    assert (own.factor, own.source) == (1.0, "row")
    assert cell_amount_scale(doc, table, 3, 1, "Total revenues", reader(), 16635)[0].factor == 1e6


def test_a_row_naming_its_currency_alone_states_no_scale_unless_the_caption_lets_it():
    # The re-check's probe: "Revenue (U.S. dollars) 1,340" in a table "(in millions)" was read in units. The word names
    # a currency, not a scale, and the caption makes no exception: held, never served on either scale.
    from kpi_extractor.rules.scale import SCALE_NOT_STATED, currency_alone
    table = Table(id="T1", context="(in millions)", rows=[["", "Q3 2026"], ["Revenue (U.S. dollars)", "1,340"],
                                                          ["Revenue (in billions)", "1.3"]])
    own, notes = cell_amount_scale(document(table), table, 1, 1, "Revenue", reader(), 1340)
    assert own.source == "reader" and notes == [SCALE_NOT_STATED]
    assert currency_alone(table, "Revenue (in billions)") is None and currency_alone(table, "Revenue (U.S. dollars)") is False
    # A per-unit KPI keeps its units whatever its row says.
    assert cell_amount_scale(document(table), table, 1, 1, "Revenue per member", reader(), 1340)[0].source == "per-unit"


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


def test_a_count_in_a_table_set_as_text_takes_its_own_column_headings_scale():
    page = "Period Processed Transactions (millions) Growth "
    assert passage_count_scale("3 Months Ended Dec 31, 2020 ", page, "Processed Transactions") == \
        ScaleFrom(1e6, "heading", "Processed Transactions (millions)")
    # Another column's heading, or the label's far up the page, scales nothing.
    other = "Payments Transactions (millions) Cash Volume ($ billions) Accounts Cards "
    assert passage_count_scale("Visa Inc. ", other, "Accounts") == ScaleFrom(1.0, "units")
    far = "Processed Transactions (millions) " + "x " * 80
    assert passage_count_scale("39,213 ", far, "Processed Transactions") == ScaleFrom(1.0, "units")


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


def test_a_heading_row_inside_the_table_states_its_rows_scale_over_the_columns_header():
    # ServiceNow's results: a column "Amount ($ millions)", then "($ billions)" over its cRPO and RPO rows.
    table = Table(id="T1", context="", rows=[["", "Amount ($ millions)", "Year/Year Growth (%)"],
                                             ["Subscription revenues", "$1,609", "28"],
                                             ["($ billions) (2)", "Year/Year Growth (%)"],
                                             ["cRPO", "$5.7", "29"], ["RPO", "$11.5", "29"]])
    doc = document(table)
    own, _notes = cell_amount_scale(doc, table, 3, 1, "Current Remaining Performance Obligations (cRPO)", reader(), 5.7)
    assert (own.factor, own.source, own.words) == (1e9, "heading", "($ billions)")
    assert cell_amount_scale(doc, table, 1, 1, "Subscription revenues", reader(), 1609)[0].factor == 1e6
    # A caption row's "except per share amounts" is no per-unit scale for the rows under it.
    caption = Table(id="T2", context="", rows=[["", "Q3"], ["($ in millions, except per share amounts)", ""], ["Revenue", "512"]])
    assert cell_amount_scale(document(caption), caption, 2, 1, "Revenue", reader(), 512)[0].factor == 1e6


def test_a_header_repeated_inside_the_table_states_the_scale_of_its_column_below_it():
    # ServiceNow's Q4 2021 results: "Amount ($ billions)" heads the cRPO column under the top's "Amount ($ millions)".
    table = Table(id="T1", context="", rows=[["", "Amount ($ millions)", "Year/Year Growth (%)"],
                                             ["Total revenues", "$1,614", "29%"],
                                             ["", "Amount ($ billions)(2)", "Year/Year Growth (%)"],
                                             ["cRPO", "$5.7", "29%"], ["RPO", "$11.5", "29%"],
                                             ["", "Amount ($ millions)", "Margin (%)"], ["Subscription gross profit", "$1,241", "82%"]])
    doc = document(table)
    assert cell_amount_scale(doc, table, 3, 1, "cRPO", reader(), 5.7)[0].factor == 1e9
    assert cell_amount_scale(doc, table, 4, 1, "RPO", reader(), 11.5)[0].factor == 1e9  # every row under the heading
    assert cell_amount_scale(doc, table, 6, 1, "Subscription gross profit", reader(), 1241)[0].factor == 1e6
    assert cell_amount_scale(doc, table, 1, 1, "Total revenues", reader(), 1614)[0].factor == 1e6


def test_a_heading_scale_ends_at_the_next_heading():
    # The re-check's probe: "($ billions)" heads the AUM rows; "Non-GAAP results:" starts rows of their own.
    table = Table(id="T1", context="($ in millions)", rows=[["", "Q3 2026"], ["Revenue", "4,100"], ["($ billions)", ""],
                                                             ["Assets under management", "908"], ["Non-GAAP results:", ""],
                                                             ["Free cash flow", "500"]])
    doc = document(table)
    assert cell_amount_scale(doc, table, 3, 1, "Assets under management", reader(), 908)[0].factor == 1e9
    found, notes = cell_amount_scale(doc, table, 5, 1, "Free cash flow", reader(), 500)
    # The caption's millions, held: whether "($ billions)" still heads the row the table does not say.
    assert found.factor == 1e6 and notes == ["a scale heading further up the table may head this row: '($ billions)'"]
    # A heading row of the caption's own scale leaves no doubt.
    same = Table(id="T2", context="($ in millions)", rows=[["", "Q3 2026"], ["Revenue", "4,100"], ["($ in millions)", ""],
                                                           ["Opex", "908"], ["Non-GAAP results:", ""], ["Free cash flow", "500"]])
    assert cell_amount_scale(document(same), same, 5, 1, "Free cash flow", reader(), 500) == (ScaleFrom(1e6, "table", "($ in millions"), [])


def test_a_per_unit_amount_never_takes_a_heading_rows_scale():
    # The re-check's probe: a caption row inside the table over FedEx's revenue per package.
    table = Table(id="T1", context="", rows=[["", "Q3 2026"], ["Packages", "16,000"],
                                             ["(in millions, except per share amounts)", ""], ["Revenue per package", "2.15"],
                                             ["Revenue", "22,200"]])
    doc = document(table)
    found, notes = cell_amount_scale(doc, table, 3, 1, "Revenue per package", reader(), 2.15)
    assert (found.factor, found.source) == (1.0, "per-unit") and not notes
    assert cell_amount_scale(doc, table, 4, 1, "Revenue", reader(), 22200)[0].factor == 1e6
