"""The earlier quarters SpaceX's first releases show beside their own: Q2 2026 as running text, Q3 2026 as a table."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.document import Document, Table, TextBlock
from kpi_extractor.release_columns import earlier_quarters, release_grids, row_key

Q2_TEXT = ("Connectivity Segment Operating and Financial Data $, in millions (except where noted) Three Months Ended Six Months "
           "Ended June 30, 2026 March 31, 2026 June 30, 2025 June 30, 2026 June 30, 2025 Starlink Subscribers (#, in millions, "
           "as of period end) 12.0 10.3 6.0 12.0 6.0 Starlink ARPU ($ per month) $66 $66 $85 $66 $85 Consumer revenues $2,485 "
           "$2,148 $1,721 $4,633 $3,213 Less: Cost of revenue $2,060 $1,651 $1,401 $3,711 $2,615 We expect growth to slow in the "
           "six months ended December 31, 2026.")
Q3_TABLE = Table("T4", "Space Segment Operating and Financial Data", [
    ["$, in millions (except where noted)", "Three Months Ended", "", "", "Nine Months Ended", ""],
    ["", "September 30, 2026", "June 30, 2026", "September 30, 2025", "September 30, 2026", "September 30, 2025"],
    ["Customer launches (#)", "12", "10", "15", "29", "36"],
    ["Launch services revenues", "$1,130", "$648", "$1,000", "$2,108", "$2,056"],
])


def test_text_and_table_releases_become_dated_grids():
    text, table = release_grids(Document("x", {"T4": Q3_TABLE}, {"P5": TextBlock("P5", Q2_TEXT)}, ["P5", "T4"]))
    assert [(c.end, c.months) for c in text.columns] == [
        (date(2026, 6, 30), 3), (date(2026, 3, 31), 3), (date(2025, 6, 30), 3), (date(2026, 6, 30), 6), (date(2025, 6, 30), 6)]
    assert [label for label, _ in text.rows] == ["Starlink Subscribers (#, in millions, as of period end)", "Starlink ARPU ($ per month)",
                                                 "Consumer revenues", "Less: Cost of revenue"]
    assert [c.months for c in table.columns] == [3, 3, 3, 9, 9] and table.rows[1][0] == "Launch services revenues"


def test_earlier_quarters_and_a_half_year_less_its_quarter_for_amounts_only():
    grid = release_grids(Document("x", {}, {"P5": TextBlock("P5", Q2_TEXT)}, ["P5"]))[0]
    revenue = dict(grid.rows)["Consumer revenues"]
    assert earlier_quarters(grid, revenue, date(2026, 6, 30), True) == {
        date(2026, 3, 31): 2148, date(2025, 6, 30): 1721, date(2025, 3, 31): 3213 - 1721}
    subscribers = dict(grid.rows)["Starlink Subscribers (#, in millions, as of period end)"]
    assert earlier_quarters(grid, subscribers, date(2026, 6, 30), False) == {date(2026, 3, 31): 10.3, date(2025, 6, 30): 6.0}
    assert row_key("Launch services revenues") == "launchservices" and row_key("Enterprise & Government (1)") == "enterpriseandgovernment"


def test_a_kpi_read_from_a_release_takes_its_earlier_columns_at_its_own_scale():
    from kpi_extractor.pipeline import SymbolPipeline
    document = Document("x", {}, {"P5": TextBlock("P5", Q2_TEXT)}, ["P5"])
    pipeline = SymbolPipeline(None, "SPCX", quarters=4, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(fiscal_year_end="1231", foreign=False)
    pipeline.offset = 0
    pipeline._document = lambda *refs: document
    subscribers = {"group_key": "kpi_operating", "kpi_key": "subs", "kpi_label": "Starlink Subscribers", "unit": "count",
                   "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30", "method": "ai", "validation_status": "verified",
                   "value": 12_000_000.0, "source_accession": "a", "locator": {"block": "P5", "quote": "Starlink Subscribers (#, in millions, as of period end) 12.0 10.3 6.0"}}
    pipeline.values = {("kpi_operating", "subs", "2026", "Q2"): subscribers}
    stored = []
    pipeline._store = lambda values: stored.extend(values)
    pipeline._fill_release_columns([SimpleNamespace(accession="a", form="8-K", filed=date(2026, 8, 4), source_url="u", period_end=date(2026, 6, 30))])
    assert sorted((v["fiscal_year"], v["fiscal_period"], v["value"]) for v in stored) == [("2025", "Q2", 6_000_000.0), ("2026", "Q1", 10_300_000.0)]
