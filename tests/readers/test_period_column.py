"""Replay finds the reported quarter's column by what its header names when last quarter's column no longer holds it."""
from datetime import date

from kpi_extractor.documents.document import Document, Table
from kpi_extractor.readers.period_column import current_column, period_column
from kpi_extractor.readers.replay import _replay_value


def doc(*rows) -> Document:
    document = Document(source_url="https://www.sec.gov/x.htm")
    document.tables["A_T1"] = Table(id="A_T1", context="", rows=[list(row) for row in rows])
    document.order.append("A_T1")
    return document


TRUIST = doc(["", "Noninterest Income", "", "", "", ""],
             ["", "Quarter Ended", "", "Change", "", ""],
             ["(Dollars in millions)", "3Q23", "2Q23", "3Q22", "Link", "Like"],
             ["Insurance income", "$793", "$935", "$725", "$(142)", "$68"],
             ["Card and payment related fees", "238", "236", "241", "2", "(3)"])


def test_a_table_once_headed_by_dates_now_by_quarter_labels_gives_this_quarters_column():
    # Truist's noninterest income: recorded under "Quarter Ended September 30, 2023" (@ #), headed "3Q23 2Q23 3Q22" now.
    last = {"row_label": "Card and payment related fees", "table": "A_T1", "row": 4, "col": 1, "header": "quarter ended @ #"}
    found = _replay_value(TRUIST, "fees.card", last, quarter=3, expected=date(2023, 9, 30))
    assert not isinstance(found, str) and (found.row, found.col) == (4, 1)
    # Last year's third quarter is not this one, nor is a change column.
    assert period_column(TRUIST.tables["A_T1"], 4, 2, last, date(2023, 9, 30), 3) == 1


def test_a_quarter_moved_one_column_on_is_followed_by_its_name():
    # U.S. Bancorp's average deposits: last quarter's "1Q 2023" was the first column; now "2Q 2023" is, "1Q 2023" beside it.
    usb = doc(["AVERAGE DEPOSITS", "", "", "", "", "", ""],
              ["($ in millions)", "", "", "", "Percent Change", "", ""],
              ["", "2Q 2023", "1Q 2023", "2Q 2022", "2Q23 vs1Q23", "YTD 2023", "YTD 2022"],
              ["Noninterest-bearing deposits", "$113,758", "$129,741", "$120,827", "(12.3)", "$121,705", "$124,375"])
    last = {"row_label": "Noninterest-bearing deposits", "table": "A_T1", "row": 3, "col": 2, "header": "average deposits <first quarter>"}
    found = _replay_value(usb, "deposits.nib", last, quarter=2, expected=date(2023, 6, 30))
    assert not isinstance(found, str) and found.col == 1


def test_a_curly_apostrophe_names_the_year_and_a_spanning_year_heads_its_column():
    # Citigroup's "3Q’23 2Q'23": the curly apostrophe read as no year, so 3Q's column failed the newest-column test.
    citi = doc(["Citigroup ($in millions, except per share amounts)", "", "3Q’23", "", "2Q'23"], ["CET1 ratio", "", "13.5%", "", "13.4%"])
    assert current_column(citi.tables["A_T1"], 1, 2)  # 2Q'23 is this year too: the quarter's name tells them apart
    # Goldman's "2026" sits one cell left of its quarter's figures.
    gs = doc(["", "", "THREE MONTHS ENDED", "", "", "", ""], ["", "2026", "", "2025", "", "", ""], ["Headcount", "", "47,500", "", "48,300", "", ""])
    assert current_column(gs.tables["A_T1"], 2, 2) and not current_column(gs.tables["A_T1"], 2, 4)


def test_no_column_is_chosen_when_two_name_the_period_or_last_quarters_was_no_quarters():
    twice = doc(["", "3Q23", "3Q23"], ["Card fees", "238", "238"])
    last = {"row_label": "Card fees", "table": "A_T1", "row": 1, "col": 9, "header": "quarter ended @ #"}
    assert period_column(twice.tables["A_T1"], 1, 9, last, date(2023, 9, 30), 3) is None
    year = {**last, "header": "year ended @ #"}
    assert period_column(TRUIST.tables["A_T1"], 4, 2, year, date(2023, 9, 30), 3) is None
    assert period_column(TRUIST.tables["A_T1"], 4, 2, {k: v for k, v in last.items() if k != "header"}, date(2023, 9, 30), 3) is None


def test_a_quarter_name_must_carry_this_fiscal_year_when_it_is_known():
    # The re-check's probe: fiscal Q1 2025 ends 30 September 2024, so "1Q24" is the year before's first quarter. With
    # this year's "1Q25" cell blank, "1Q24" was the one column left and was read as this quarter's.
    blank = doc(["(Dollars in millions)", "1Q25", "1Q24"], ["Card fees", "", "238"])
    last = {"row_label": "Card fees", "table": "A_T1", "row": 1, "col": 2, "header": "quarter ended @ #"}
    end = date(2024, 9, 30)
    assert period_column(blank.tables["A_T1"], 1, 2, last, end, 1) == 2  # unknown fiscal year: this year or the next
    assert period_column(blank.tables["A_T1"], 1, 2, last, end, 1, fiscal_year=2025) is None
    found = _replay_value(blank, "fees.card", {**last, "col": 9}, quarter=1, expected=end, fiscal_year=2025)
    assert isinstance(found, str)
    # This fiscal year's own column stands.
    named = doc(["(Dollars in millions)", "1Q25", "1Q24"], ["Card fees", "241", "238"])
    assert period_column(named.tables["A_T1"], 1, 9, last, end, 1, fiscal_year=2025) == 1
