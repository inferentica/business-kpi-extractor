"""quality.list_checks and a first list's settling (p4-listchecks): checks feed Left out, never hold extraction back."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.ai import Spec
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.quality.list_checks import (REASON_SECTOR, REASON_UNREAD, noted_unreported, refused_lines,
                                              sector_lines_missing, unread_lines)

BANK = Spec.model_validate({"groups": [{"key": "ratios", "label": "Key Ratios", "kind": "metric", "kpis": [
    {"key": "nim", "label": "Net Interest Margin", "unit": "percent"}, {"key": "cet1", "label": "CET1 Ratio", "unit": "percent"}]}]})


def test_a_sector_line_the_filing_names_and_the_list_lacks_is_left_out():
    text = "Net interest margin was 3.1%. The efficiency ratio was 58.2%. CET1 was 11.0%."
    lines = sector_lines_missing("bank", text, BANK)
    assert [(line["label"], line["unit"], line["reason"], line["source"]) for line in lines] == [
        ("Efficiency ratio", "percent", REASON_SECTOR, "rule")]
    assert sector_lines_missing("bank", "Net interest margin was 3.1%.", BANK) == []  # not named: nothing to suggest
    assert sector_lines_missing("software", text, BANK) == []


def test_lines_no_newest_filing_has_are_unread():
    assert unread_lines(BANK, [{"ratios.cet1"}, {"ratios.cet1", "ratios.nim"}]) == {("ratios", "cet1")}
    # A line one filing found is read (stored under a series' key or not), and a filing nothing read says nothing.
    assert unread_lines(BANK, [{"ratios.cet1"}, set()]) == set()
    assert unread_lines(BANK, [None, None]) == set()
    assert unread_lines(BANK, [None, {"ratios.cet1"}]) == {("ratios", "cet1")}


def test_a_line_a_reader_found_and_a_check_refused_is_in_the_filing():
    problems = ["flash ratios.cet1: column headed 2025, not the quarter ended 2026-09-30",
                "pro ratios.cet1: column headed 2025, not the quarter ended 2026-09-30",
                "ratios.nim: quote names another period", "breakdown reports the full year: stored as the fiscal year"]
    assert refused_lines(problems) == {"ratios.cet1", "ratios.nim"}
    # A filing read before: its note named the refused line as not reported, and its notes give the refusal.
    notes = ["read by AI", "release reader 5", "not reported: ratios.cet1, ratios.nim", problems[0]]
    assert noted_unreported(notes) == {"ratios.nim"}
    assert noted_unreported(["read by AI"]) == set() and noted_unreported(["xbrl reader 50"]) is None


def _pipeline():
    pipeline = SymbolPipeline(SimpleNamespace(call=lambda *a, **k: {}), "CAT", quarters=40, force=False, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="Caterpillar", fiscal_year_end="1231", foreign=False, sic="3531")
    pipeline.names = {}
    pipeline.store._filings.clear()
    processed, stored = [], []
    pipeline._process_releases = lambda refs, spec, version: processed.append(([ref.period_end for ref in refs], spec)) or list(refs)
    pipeline._store_spec = lambda spec, names, version, accession: stored.append((spec, list(pipeline.__dict__.get("_left_out", []))))
    ends = [date(year, month, day) for year in (2025, 2026) for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))]
    releases = [SimpleNamespace(period_end=end, accession=f"a{i}") for i, end in enumerate(ends)]
    return pipeline, releases, processed, stored


def test_a_first_list_reads_its_whole_history_and_its_unread_lines_go_to_left_out():
    pipeline, releases, processed, stored = _pipeline()
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI", "not reported: ratios.cet1"]}
    kept, done, spec = pipeline._settle_first_list({}, releases, BANK, 1, True)
    assert len(kept) == 8 and [len(batch) for batch, _spec in processed] == [4, 4]
    assert [kpi.key for group in spec.groups for kpi in group.kpis] == ["nim"]  # the rest of the history without it
    assert [kpi.key for group in processed[1][1].groups for kpi in group.kpis] == ["nim"]
    assert [(line["key"], line["reason"]) for line in stored[-1][1]] == [("cet1", REASON_UNREAD)]
    assert pipeline.result.lines_not_found == ["ratios.cet1"]


def test_a_first_list_whose_lines_are_all_read_is_unchanged():
    pipeline, releases, processed, stored = _pipeline()
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI"]}
    kept, done, spec = pipeline._settle_first_list({}, releases, BANK, 1, True)
    assert spec is BANK and len(kept) == 8 and stored == [] and pipeline.result.lines_not_found == []


def test_a_list_flagged_before_reads_its_whole_history_and_drops_its_flag():
    pipeline, releases, processed, stored = _pipeline()
    for ref in releases:
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI"]}
    state = {"spec": {"checks": [{"check": "unread_lines", "text": "x"}]}}
    kept, done, spec = pipeline._settle_first_list(state, releases, BANK, 2, False)
    assert len(kept) == 8 and len(stored) == 1  # stored again: the spec now sends no checks
    # A stored list with no flag reads as before, in one pass.
    pipeline, releases, processed, stored = _pipeline()
    kept, done, spec = pipeline._settle_first_list({"spec": {"checks": []}}, releases, BANK, 2, False)
    assert len(processed) == 1 and stored == []


def test_this_runs_readings_count_in_full_over_the_cut_note():
    pipeline, releases, processed, stored = _pipeline()
    for ref in releases:
        # The stored note was cut before ratios.cet1; the reading itself gave it in full.
        pipeline.store._filings[ref.accession] = {"accession": ref.accession, "notes": ["read by AI", "not reported: ratios.ni"]}
        pipeline.store.unreported[ref.accession] = frozenset({"ratios.nim", "ratios.cet1"})
    kept, done, spec = pipeline._settle_first_list({}, releases, BANK, 1, True)
    assert pipeline.result.lines_not_found == ["ratios.cet1", "ratios.nim"] and spec.groups == []
