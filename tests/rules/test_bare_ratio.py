"""rules.table.not_a_percent: a ratio's row printed without its % sign."""
from kpi_extractor.documents.document import Table
from kpi_extractor.rules.table import not_a_percent

HEAD = [["", "Quarter ended", ""], ["(in millions, except ratios and per share amounts)", "Mar 31,2023", "Dec 31,2022"]]


def test_a_ratio_row_of_small_figures_is_a_percent():
    # Wells Fargo's summary: no % sign on the page.
    table = Table("B_T2", "", HEAD + [["Total revenue", "$20,729", "20,034"], ["Efficiency ratio (7)", "66", "81"]])
    assert not_a_percent(table, 3, 1, "66") is None


def test_amounts_multiples_and_other_rows_are_not():
    table = Table("B_T2", "", HEAD + [["Total loans", "335,644", "333,000"], ["Coverage ratio", "1.2x", "1.3x"],
                                      ["Loan to deposit ratio", "1,050", "98"], ["Net interest margin", "3.1", "3.0"]])
    assert not_a_percent(table, 2, 1, "335,644") == "'335,644' is not a percentage"
    assert not_a_percent(table, 3, 1, "1.2x") == "'1.2x' is not a percentage"
    assert not_a_percent(table, 4, 2, "98") == "'98' is not a percentage"  # a figure in its row is no percent
    assert not_a_percent(table, 5, 1, "3.1") == "'3.1' is not a percentage"  # not named a ratio: as before


def test_a_refused_ratio_quarter_with_no_served_figure_is_read_again():
    from kpi_extractor.ops.reread import picked_filings
    refused = "flash operating.efficiency_ratio: '66' is not a percentage"
    filings = {"q1": {"period_end": "2023-03-31", "notes": ["read by AI", refused]},
               "q3": {"period_end": "2023-09-30", "notes": ["read by AI", refused.replace("66", "63")]},
               "q4": {"period_end": "2023-12-31", "notes": ["read by AI"]}}
    values = {1: {"group_key": "kpi_operating", "kpi_key": "efficiency_ratio", "period_end": "2023-09-30",
                  "validation_status": "verified"}}
    assert picked_filings("bare-ratios", values, filings) == {"q1"}
