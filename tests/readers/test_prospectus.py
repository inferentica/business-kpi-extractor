"""SpaceX's IPO prospectus (424B4, June 2026): revenue by type for full years and first quarters, its AI segment named
SI by its second 10-Q."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.documents.document import Document, Table
from kpi_extractor.readers.prospectus import Period, interim_quarters, period_columns, prospectus_breakdowns, scale_for

YEARS = Table("T68", "All of products revenue is attributable to the Connectivity segment. (in millions)", [
    ["", "Year Ended December 31,", "", ""],
    ["", "2025", "2024", "2023"],
    ["Launch Services ..........", "$2,576", "$2,584", "$1,964"],
    ["Space ..........", "4,086", "3,796", "3,557"],
    ["Consumer ..........", "7,208", "4,830", "2,817"],
    ["Enterprise & Government (1) ..........", "4,179", "2,769", "1,052"],
    ["Connectivity ..........", "11,387", "7,599", "3,869"],
    ["Launch & Development ..........", "1,510", "1,212", "1,593"],
    ["AI ..........", "3,201", "2,620", "2,961"],
    ["Total revenues ..........", "$18,674", "$14,015", "$10,387"],
])
# Quarters beside years under one header row, its spans carried across the columns under them; no scale stated.
MIXED = Table("T9", "The following table presents revenue by segment:", [
    ["", "Three Months Ended March 31,", "", "Year Ended December 31,", ""],
    ["", "2026", "2025", "2025", "2024"],
    ["Space ....", "$619", "$865", "$4,086", "$3,796"],
    ["Connectivity ....", "3,257", "2,475", "11,387", "7,599"],
    ["AI ....", "818", "727", "3,201", "2,620"],
    ["Total revenues ....", "$4,694", "$4,067", "$18,674", "$14,015"],
])
SEGMENTS = {"space": "Space", "connectivity": "Connectivity", "si": "SI"}


def test_each_column_takes_the_span_above_it():
    assert period_columns(MIXED) == {1: Period(date(2026, 3, 31), False), 2: Period(date(2025, 3, 31), False),
                                     3: Period(date(2025, 12, 31), True), 4: Period(date(2024, 12, 31), True)}
    assert interim_quarters(Document("x", {"T9": MIXED}, {}, ["T9"])) == [date(2026, 3, 31), date(2025, 3, 31)]


def test_a_renamed_segment_is_the_one_row_left_equal_to_the_rest_of_revenue():
    found = prospectus_breakdowns(Document("x", {"T68": YEARS}, {}, ["T68"]), SEGMENTS, 7_814e6)
    assert found[Period(date(2025, 12, 31), True)].values == {"space": 4_086e6, "connectivity": 11_387e6, "si": 3_201e6}
    assert found[Period(date(2023, 12, 31), True)].rows["si"] == "AI"
    # With no scale stated, it is the one that puts the quarter beside the reported one.
    mixed = prospectus_breakdowns(Document("x", {"T9": MIXED}, {}, ["T9"]), SEGMENTS, 7_814e6)
    assert mixed[Period(date(2025, 3, 31), False)].values["si"] == 727e6 and len(mixed) == 4
    assert scale_for(4_694, 7_814e6, False) == 1e6 and scale_for(18_674, 7_814e6, True) == 1e6


def test_a_table_missing_two_lines_or_not_adding_up_is_not_read():
    assert prospectus_breakdowns(Document("x", {"T9": MIXED}, {}, ["T9"]), {**SEGMENTS, "x": "Other bets"}, 7_814e6) == {}
    broken = Table("T9", MIXED.context, [row[:] for row in MIXED.rows])
    broken.rows[2][1] = "$600"
    found = prospectus_breakdowns(Document("x", {"T9": broken}, {}, ["T9"]), {"space": "Space", "connectivity": "Connectivity", "ai": "AI"}, 7_814e6)
    assert Period(date(2026, 3, 31), False) not in found and Period(date(2025, 3, 31), False) in found


def test_a_first_reports_year_ago_quarter_is_kept_when_it_adds_up():
    from kpi_extractor.pipeline import SymbolPipeline

    def row(key, value, prior):
        return {"group_key": "segments", "kpi_key": key, "fiscal_year": "2026", "fiscal_period": "Q2", "period_end": "2026-06-30",
                "method": "xbrl", "validation_status": "verified", "value": value, "unit": "currency",
                "locator": {"prior": prior, "prior_total": 4_071e6, "total": 7_814e6}}
    pipeline = SymbolPipeline(None, "SPCX", quarters=4, force=False, log=lambda *_: None)
    pipeline.values = {("segments", k, "2026", "Q2"): row(k, v, p) for k, v, p in
                       (("space", 962e6, 746e6), ("connectivity", 4_291e6, 2_588e6), ("si", 2_561e6, 737e6))}
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.write(pipeline._year_ago_quarters())
    assert {(v["kpi_key"], v["fiscal_year"], v["period_end"], v["value"]) for v in stored} == {
        ("space", "2025", "2025-06-30", 746e6), ("connectivity", "2025", "2025-06-30", 2_588e6), ("si", "2025", "2025-06-30", 737e6)}
    stored.clear()
    pipeline.values[("segments", "si", "2026", "Q2")]["locator"]["prior"] = 700e6  # no longer adds up
    pipeline.store.write(pipeline._year_ago_quarters())
    assert stored == []


def test_the_prospectus_name_of_a_renamed_line_joins_its_earlier_quarters():
    from kpi_extractor.pipeline import SymbolPipeline

    def row(key, label, quarter, value):
        return {"group_key": "segments", "kpi_key": key, "kpi_label": label, "fiscal_year": "2026", "fiscal_period": quarter,
                "period_end": "2026-06-30", "method": "xbrl", "validation_status": "verified", "value": value, "notes": []}
    pipeline = SymbolPipeline(None, "SPCX", quarters=4, force=False, log=lambda *_: None)
    pipeline.values = {("segments", k, "2026", q): row(k, l, q, v) for k, l, q, v in
                       (("ai", "AI", "Q2", 2_561e6), ("si", "SI", "Q3", 7_560e6))}
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.write(pipeline._join_prospectus_names({("segments", "ai"): "si"}))
    assert [(v["kpi_key"], v["kpi_label"], v["validation_status"]) for v in stored] == [("si", "SI", "verified"), ("ai", "AI", "rejected")]


def test_a_quarters_reading_gets_the_passages_naming_a_kpi_and_that_quarter():
    from kpi_extractor.documents.document import TextBlock
    from kpi_extractor.readers.prospectus import quarter_sections
    blocks = {"B1": TextBlock("B1", "Starlink Subscribers may churn if service quality declines."),
              "B2": TextBlock("B2", "Starlink Subscribers totaled approximately 10.3 million and 5.0 million in the quarters ended March 31, 2026 and March 31, 2025."),
              "B3": TextBlock("B3", "As of March 31, 2026, we had 9,600 satellites in orbit.")}
    sections = quarter_sections(Document("x", {}, blocks, ["B1", "B2", "B3"]), ["Starlink Subscribers"], date(2026, 3, 31))
    assert sections.order == ["B2"]


def _segment_table(table_id: str, heading: str, label: str, figures: list[str]) -> Table:
    return Table(table_id, f"Segment Operating and Financial Data (unaudited) {heading}", [
        ["", "Three Months Ended March 31,", "", "", "Year Ended December 31,", "", ""],
        ["", "2026", "2025", "", "2025", "2024", "2023"],
        ["Launches (number) ..........", "40", "38", "", "170", "138", "98"],
        [f"{label} (in millions) ..........", *figures[:2], "", *figures[2:]],
    ])


# SpaceX's prospectus: one table per segment under its name, its AI segment SI by the second 10-Q.
SEGMENT_TABLES = {
    "T16": _segment_table("T16", "Space:", "Segment income (loss) from operations", ["$(662)", "$(70)", "$(657)", "$21", "$(1)"]),
    "T17": _segment_table("T17", "Space: Connectivity:", "Segment income from operations", ["$1,188", "$1,033", "$4,423", "$2,006", "$469"]),
    "T18": _segment_table("T18", "Connectivity: AI:", "Segment loss from operations", ["$(2,469)", "$(936)", "$(6,355)", "$(1,561)", "$(3,973)"]),
}


def test_each_segments_income_is_read_from_its_own_table_under_its_name():
    from kpi_extractor.readers.prospectus import prospectus_segment_income
    document = Document("x", SEGMENT_TABLES, {}, list(SEGMENT_TABLES))
    found = prospectus_segment_income(document, SEGMENTS, {"ai": "si"})
    assert found[Period(date(2026, 3, 31), False)].values == {"space": -662e6, "connectivity": 1_188e6, "si": -2_469e6}
    assert found[Period(date(2023, 12, 31), True)].values == {"space": -1e6, "connectivity": 469e6, "si": -3_973e6}
    assert found[Period(date(2026, 3, 31), False)].names["si"] == "AI"
    # Without the revenue tables' rename, the one segment left is the one table left.
    assert prospectus_segment_income(document, SEGMENTS)[Period(date(2025, 3, 31), False)].values["si"] == -936e6
    # A segment the prospectus does not give: no period is read.
    two = Document("x", {key: SEGMENT_TABLES[key] for key in ("T16", "T17")}, {}, ["T16", "T17"])
    assert prospectus_segment_income(two, SEGMENTS) == {}


def test_the_prospectus_gives_segment_income_and_joins_the_renamed_segments_quarters():
    from kpi_extractor.pipeline import SymbolPipeline
    from kpi_extractor.sources.base import FilingRef

    def row(key, label, year, quarter, value, end):
        return {"group_key": "segment_income", "group_label": "Operating Income by Segment", "group_kind": "revenue_breakdown",
                "group_order": 50, "kpi_key": key, "kpi_label": label, "kpi_order": 0, "fiscal_year": year, "fiscal_period": quarter,
                "period_end": end, "method": "xbrl", "validation_status": "verified", "value": value, "notes": [], "currency": "USD",
                "locator": {"member": f"spcx:{label}Member"}}
    pipeline = SymbolPipeline(None, "SPCX", quarters=8, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(fiscal_year_end="1231")
    pipeline.offset = 0
    stored_rows = [row("space", "Space", "2026", "Q3", -7e6, "2026-09-30"), row("connectivity", "Connectivity", "2026", "Q3", 2_367e6, "2026-09-30"),
                   row("si", "SI", "2026", "Q3", 431e6, "2026-09-30"), row("ai", "AI", "2026", "Q2", -1_257e6, "2026-06-30")]
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in stored_rows}
    ref = FilingRef("0001628280-26-042639", "424B4", date(2026, 6, 12), "earnings_release", "https://www.sec.gov/x.htm")
    renamed = {("segments", "ai"): "si"}
    records = pipeline._prospectus_segment_income(Document("x", SEGMENT_TABLES, {}, list(SEGMENT_TABLES)), ref, renamed)
    by = {(r["kpi_key"], r["fiscal_year"], r["fiscal_period"]): r["value"] for r in records}
    assert by[("si", "2026", "Q1")] == -2_469e6 and by[("space", "2025", "FY")] == -657e6 and len(records) == 15
    assert renamed[("segment_income", "ai")] == "si"
    stored = []
    pipeline.store.write = stored.extend
    pipeline.store.write(pipeline._join_prospectus_names({("segment_income", "ai"): "si"}))
    assert [(v["kpi_key"], v["fiscal_period"], v["validation_status"]) for v in stored] == [("si", "Q2", "verified"), ("ai", "Q2", "rejected")]
