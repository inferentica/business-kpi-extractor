"""quality.list_checks and the first-quarters gate (p4-listchecks)."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.quality.list_checks import sector_lines_missing, unread_lines

BANK = Spec.model_validate({"groups": [{"key": "ratios", "label": "Key Ratios", "kind": "metric", "kpis": [
    {"key": "nim", "label": "Net Interest Margin", "unit": "percent"}, {"key": "cet1", "label": "CET1 Ratio", "unit": "percent"}]}]})


def test_a_sector_line_the_filing_names_and_the_list_lacks_is_flagged():
    text = "Net interest margin was 3.1%. The efficiency ratio was 58.2%. CET1 was 11.0%."
    flags = sector_lines_missing("bank", text, BANK)
    assert [flag["text"] for flag in flags] == ["The filing gives the efficiency ratio; the list has no line for it."]
    assert sector_lines_missing("bank", "Net interest margin was 3.1%.", BANK) == []  # not named: nothing to ask
    assert sector_lines_missing("software", text, BANK) == []


def test_lines_every_newest_filing_notes_as_not_reported_are_flagged():
    notes = [["read by AI", "not reported: ratios.cet1"], ["read by AI", "not reported: ratios.cet1, ratios.nim"]]
    flag = unread_lines(BANK, notes, 2)
    assert flag["lines"] == ["CET1 Ratio"] and flag["check"] == "unread_lines"
    # A line one filing found is read (stored under a series' key or not), and a filing nothing read says nothing.
    assert unread_lines(BANK, [["read by AI", "not reported: ratios.cet1"], ["read by AI"]], 2) is None
    assert unread_lines(BANK, [[], []], 2) is None


def _pipeline(state_spec):
    pipeline = SymbolPipeline(SimpleNamespace(call=lambda *a, **k: {}), "CAT", quarters=40, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Caterpillar", fiscal_year_end="1231", foreign=False, sic="3531")
    pipeline.offset = 0
    pipeline.names = {}
    pipeline.values = {}
    pipeline.store._filings.clear()
    processed = []
    pipeline._process_releases = lambda refs, spec, version: processed.append([ref.period_end for ref in refs]) or list(refs)
    stored = []
    pipeline._store_spec = lambda spec, names, version, accession: stored.append(pipeline.list_checks)
    ends = [date(year, month, day) for year in (2025, 2026) for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))]
    releases = [SimpleNamespace(period_end=end, accession=f"a{i}") for i, end in enumerate(ends)]
    return pipeline, releases, processed, stored, state_spec


def test_a_first_list_that_passes_reads_every_release():
    pipeline, releases, processed, stored, state = _pipeline(None)
    pipeline.list_checks = []
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI"]}
    kept, done = pipeline._first_quarters_gate({}, releases, BANK, 1, True)
    assert len(kept) == 8 and [len(batch) for batch in processed] == [4, 4] and stored == [[]]
    assert pipeline.result.list_flags == []


def test_a_flagged_first_list_stops_at_its_newest_quarters():
    pipeline, releases, processed, stored, state = _pipeline(None)
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession,
                                                  "notes": ["read by AI", "not reported: ratios.nim, ratios.cet1"]}
    pipeline.list_checks = [{"check": "sector_line", "text": "The filing gives the efficiency ratio; the list has no line for it."}]
    kept, done = pipeline._first_quarters_gate({}, releases, BANK, 1, True)
    assert [ref.period_end for ref in kept] == [ref.period_end for ref in releases[-4:]] and len(processed) == 1
    assert [flag["check"] for flag in pipeline.result.list_flags] == ["sector_line", "unread_lines"]
    assert stored and len(stored[-1]) == 2


def test_open_checks_keep_a_stored_list_at_its_newest_quarters_and_an_accepted_one_reads_all():
    pipeline, releases, processed, stored, _ = _pipeline(None)
    kept, _done = pipeline._first_quarters_gate({"spec": {"checks": [{"check": "sector_line", "text": "x"}]}}, releases, BANK, 2, False)
    assert len(kept) == 4 and stored == []
    # An unread-lines flag the newest filings no longer bear out is dropped, and the list reads its history.
    pipeline, releases, processed, stored, _ = _pipeline(None)
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI"]}
    kept, _done = pipeline._first_quarters_gate({"spec": {"checks": [{"check": "unread_lines", "text": "y"}]}}, releases, BANK, 2, False)
    assert len(kept) == 8 and stored == [[]]
    pipeline, releases, processed, stored, _ = _pipeline(None)
    kept, _done = pipeline._first_quarters_gate({"spec": {"checks": []}}, releases, BANK, 2, False)
    assert len(kept) == 8 and processed == [[ref.period_end for ref in releases]]
