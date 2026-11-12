from kpi_extractor.documents.document import parse_document
from kpi_extractor.readers.quoted_rows import replay_row_line, row_line, row_line_others
from kpi_extractor.readers.replay import _replay_quote


def test_a_quoted_row_is_its_label_and_figures():
    row = row_line("Total management fees 505 561 577 14.3% 1,929 2,206 14.4%", "577")
    assert (row.label, row.index, len(row.slots)) == ("Total management fees", 2, 7)
    assert row_line("Gross outflows4,5 (6,879) (29,868)", "(6,879)").label == "Gross outflows"  # footnote marks set aside
    assert row_line("Perpetual Capital AUM ($bn) $302 $341 $395", "$395").label == "Perpetual Capital AUM ($bn)"
    assert row_line("the average price per ad decreased by 6% year-over-year", "6%") is None  # a sentence, not a row
    assert row_line("Equity 192 192 189", "192") is None  # which 192 was read is not recorded
    # The row's other figures, not its percentages: the year-ago one among them.
    assert row_line_others("Total management fees 505 561 577 14.3% 1,929 2,206 14.4%", "577") == [505, 561, 1929, 2206]


def test_a_quoted_row_whose_sign_or_footnote_changed_is_found_again_by_its_label_and_place():
    # Apollo: "Outflows2" became "Outflows3", and a fall in parentheses became a rise.
    document = parse_document("<p>Assets Under Management Inflows 25,774 3,458 Outflows3 1,658 (263) (509) 886 "
                              "Net Flows 12,116 3,195</p>", "https://www.sec.gov/x.htm")
    last = {"quote": "Outflows2 (11,045) (291) (3) (11,339)", "value_text": "(11,339)", "block": "A_P0"}
    found = _replay_quote(document, "flows.outflows", last)
    assert found.quote == "Outflows3 1,658 (263) (509) 886" and found.value_text == "886"


def test_a_quoted_row_held_twice_in_the_filing_is_not_read():
    # A quarter's row and a year to date's of the same shape: which is the quarter's is not known.
    document = parse_document("<p>2Q'23 Outflows3 (13,658) (263) (509) (14,430)</p><p>YTD'23 Outflows3 (38,663) (2,031) "
                              "(1,668) (42,362)</p>", "https://www.sec.gov/x.htm")
    last = {"quote": "Outflows2 11,045 291 3 11,339", "value_text": "11,339", "block": "A_P0"}
    assert replay_row_line(document, "flows.outflows", last) == "row found more than once"
    # Nor a row with more figures than last quarter's: its places are not the same.
    wider = parse_document("<p>Outflows3 (13,658) (263) (509) (14,430) (1)</p>", "https://www.sec.gov/x.htm")
    assert replay_row_line(wider, "flows.outflows", last) == "sentence not found"


def test_a_quoted_rows_other_figures_are_its_year_ago_cells():
    # The confirmation's year-ago check reads them as a table row's other cells: a replayed row stands where the
    # filing's own year-ago figure equals the one stored for that quarter.
    from kpi_extractor.steps.shared import _year_ago_cells
    locator = {"block": "A_P0", "quote": "Perpetual Capital AUM ($bn) $ 307 $351 $409", "value_text": "$409"}
    assert _year_ago_cells(parse_document("<p>x</p>", "https://www.sec.gov/x.htm"), locator, 409e9) == [307e9, 351e9]
    document = parse_document("<p>Perpetual Capital AUM ($bn) $ 314 $352 $430 Athene</p>", "https://www.sec.gov/x.htm")
    found = replay_row_line(document, "aum.perpetual", locator)
    assert found.value_text == "$430"


def test_a_dollar_sign_set_apart_belongs_to_its_figure():
    # KKR's decks print "Management Fees $ 654,927 $ 748,888 ...": the row and its year-ago figure are read.
    from kpi_extractor.readers.quoted_rows import row_line, row_line_others
    row = row_line("Management Fees $ 654,927 $ 748,888 $ 1,279,855 $ 1,487,044", "748,888")
    assert row is not None and row.label == "Management Fees" and row.index == 1 and len(row.slots) == 4
    assert row_line_others("Management Fees $ 654,927 $ 748,888 $ 1,279,855 $ 1,487,044", "748,888") == [654_927, 1_279_855, 1_487_044]
