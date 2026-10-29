"""SpaceX's IPO prospectus (424B4, June 2026): revenue by type for full years and first quarters, its AI segment named
SI by its second 10-Q."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.document import Document, Table
from kpi_extractor.prospectus import Period, interim_quarters, period_columns, prospectus_breakdowns, scale_for

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
    pipeline._store = lambda values: stored.extend(values)
    pipeline._year_ago_quarters()
    assert {(v["kpi_key"], v["fiscal_year"], v["period_end"], v["value"]) for v in stored} == {
        ("space", "2025", "2025-06-30", 746e6), ("connectivity", "2025", "2025-06-30", 2_588e6), ("si", "2025", "2025-06-30", 737e6)}
    stored.clear()
    pipeline.values[("segments", "si", "2026", "Q2")]["locator"]["prior"] = 700e6  # no longer adds up
    pipeline._year_ago_quarters()
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
    pipeline._store = lambda values: stored.extend(values)
    pipeline._join_prospectus_names({("segments", "ai"): "si"})
    assert [(v["kpi_key"], v["kpi_label"], v["validation_status"]) for v in stored] == [("si", "SI", "verified"), ("ai", "AI", "rejected")]
