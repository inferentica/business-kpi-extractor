"""A backfill's first year is read in date order before the four chains go on side by side."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.pipeline import SymbolPipeline


class _Control:
    def call(self, operation, **payload):
        return {}


def _refs():
    ends = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31),
            date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)]
    return [SimpleNamespace(accession=end.isoformat(), period_end=end) for end in reversed(ends)]


def test_each_quarter_of_a_backfills_first_year_is_read_after_the_quarter_before_it():
    pipeline = SymbolPipeline(_Control(), "WFC", quarters=8, force=True, log=lambda *_: None)
    pipeline.values, pipeline.filings = {}, {}
    pipeline._never_verified = lambda: set()
    pipeline._check_read_series = lambda keys: []
    pipeline.store.write = lambda rows: None
    read: list[tuple[str, bool]] = []

    def record(ref, spec, version):
        read.append((ref.accession, bool(getattr(pipeline._local, "lead", False))))
        return True

    pipeline._read_and_record_release = record
    done = pipeline._process_releases(_refs(), SimpleNamespace(), 1)
    assert len(done) == 8
    # The first four, alone and in date order: each one's reference is the quarter just before it, not nothing.
    assert read[:4] == [("2024-03-31", True), ("2024-06-30", True), ("2024-09-30", True), ("2024-12-31", True)]
    # The chains then go on: each later quarter after the same quarter a year earlier, never as a lead quarter.
    assert sorted(read[4:]) == [("2025-03-31", False), ("2025-06-30", False), ("2025-09-30", False), ("2025-12-31", False)]


def test_a_lead_quarter_takes_its_reference_from_another_chain_and_a_chained_one_does_not():
    pipeline = SymbolPipeline(_Control(), "WFC", quarters=8, force=True, log=lambda *_: None)
    pipeline.filings = {}
    pipeline.values = {("kpi_loans", "total", "2024", "Q1"): {
        "group_key": "kpi_loans", "kpi_key": "total", "fiscal_year": "2024", "fiscal_period": "Q1", "period_end": "2024-03-31",
        "method": "ai", "validation_status": "verified", "value": 910.0, "locator": {"row_label": "Total loans"},
        "source_url": "https://www.sec.gov/x.htm"}}
    pipeline._local.chained = True
    assert pipeline._ai_history(date(2024, 6, 30)) == {}  # another chain's quarter, read side by side: never a reference
    pipeline._local.lead = True
    assert set(pipeline._ai_history(date(2024, 6, 30))) == {"loans.total"}
    assert pipeline._earlier_values(date(2024, 6, 30)) == {"loans.total": [910.0]}
