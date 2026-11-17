"""Selective re-reads: each selector picks the filings a rule change can affect, once, before anything is read again."""
from types import SimpleNamespace

import pytest

from kpi_extractor.ops.cli import parse_args
from kpi_extractor.ops.reread import picked_filings
from kpi_extractor.steps.loading import FilingLoading


def held(accession, note, **extra):
    return {"method": "ai", "validation_status": "needs_review", "source_accession": accession, "notes": [note],
            "locator": {"ai_call": "c1"}, **extra}


def test_size_holds_picks_the_filings_holding_a_figure_held_for_its_size_alone():
    values = {1: held("JUMP", "changed 2.4x in a quarter"),
              2: held("PART", "fewer rows and under half the total of the quarter compared: likely part of the table"),
              3: held("SUMS", "parts differ from the total by 4%"),  # a sum that fails is not a size
              4: held("OWNER", "changed 2.4x in a quarter", owner_decision="confirmed"),  # the owner settled it
              5: {**held("SERVED", "changed 2.4x in a quarter"), "validation_status": "verified"}}
    assert picked_filings("size-holds", values) == {"JUMP", "PART"}


def test_unit_price_rows_picks_prices_read_with_a_scale_and_section_place_holds():
    row = lambda accession, status, factor, label="Brent ($/b)", **extra: {
        "method": "ai", "validation_status": status, "source_accession": accession, "notes": [],
        "locator": {"row_label": label, "scale_from": {"factor": factor}}, **extra}
    values = {1: row("SCALED", "verified", 1e6),  # served at the document's "in millions"
              2: row("HELD", "needs_review", 1),
              3: row("RIGHT", "verified", 1),
              4: row("REVENUE", "verified", 1e6, label="Total revenues"),
              5: row("OWNER", "verified", 1e6, owner_decision="confirmed"),
              6: held("PLACE", "the row and its headings name no: US")}
    assert picked_filings("unit-price-rows", values) == {"SCALED", "HELD", "PLACE"}


def test_an_unknown_selector_is_refused():
    with pytest.raises(ValueError, match="Unknown re-read selector"):
        picked_filings("everything", {})
    with pytest.raises(SystemExit):
        parse_args(["--reread", "everything"])


def test_the_pick_is_made_once_before_anything_is_read_again():
    values = {1: held("JUMP", "changed 2.4x in a quarter")}
    run = SimpleNamespace(values=values, reread="size-holds", log=lambda *_: None)
    assert FilingLoading._reread_due(run, "JUMP")
    values[1]["validation_status"] = "verified"  # the re-read now confirms it: the run's pick does not change
    assert FilingLoading._reread_due(run, "JUMP") and not FilingLoading._reread_due(run, "OTHER")


def test_a_retrace_is_the_untraced_selector():
    from kpi_extractor.pipeline import SymbolPipeline
    control = SimpleNamespace(call=lambda *_, **__: {}, ai=None)
    def run(**options):
        return SymbolPipeline(control, "TSM", quarters=4, force=False, log=lambda *_: None, **options).reread
    assert run(retrace=True) == "untraced" and run(reread="size-holds") == "size-holds" and run() is None
    assert parse_args(["--reread", "size-holds"]).reread == "size-holds"


def test_a_selective_run_reads_only_what_its_selector_picked():
    from kpi_extractor.steps.release_flow import ReleaseFlow
    values = {1: held("JUMP", "changed 2.4x in a quarter")}
    run = SimpleNamespace(values=values, reread="size-holds", log=lambda *_: None, filings={}, force=False)
    run._reread_due = lambda accession: FilingLoading._reread_due(run, accession)
    picked, new = SimpleNamespace(accession="JUMP", role="earnings_release"), SimpleNamespace(accession="NEW", role="periodic_report")
    # A filing never read is due in an ordinary run; in a selective one it waits for an ordinary run.
    assert FilingLoading._pending(run, picked) and not FilingLoading._pending(run, new)
    # A release due for another reason (here: never read) is not read either.
    assert ReleaseFlow._read_release(run, SimpleNamespace(accession="OTHER"), None, 2) is None
    ordinary = SimpleNamespace(values=values, reread=None, filings={})
    assert FilingLoading._pending(ordinary, new)


def test_a_years_report_keeps_breakdowns_but_no_metric_figure():
    from kpi_extractor.ai import Spec
    from kpi_extractor.steps.release_flow import spec_kind, year_figures
    spec = Spec.model_validate({"groups": [
        {"key": "operating", "label": "Operating Metrics", "kind": "metric", "kpis": [{"key": "systems", "label": "Systems sold", "unit": "count"}]},
        {"key": "products", "label": "Products", "kind": "revenue_breakdown", "kpis": [{"key": "euv", "label": "EUV", "unit": "currency"}]}]})
    metric, part = (SimpleNamespace(group=group) for group in spec.groups)
    assert year_figures([metric, part]) == [part]
    assert (spec_kind(spec, "operating.systems"), spec_kind(spec, "products.euv")) == ("metric", "revenue_breakdown")


def test_repeat_holds_picks_figures_held_for_repeating_an_earlier_period():
    values = {1: held("APO", "the same figure as a year earlier: check the column"),
              2: held("KKR", "the same figure as the quarter before: check the column"),
              3: held("SUMS", "parts differ from the total by 4%")}
    assert picked_filings("repeat-holds", values) == {"APO", "KKR"}


def test_sum_holds_picks_breakdowns_whose_parts_did_not_add_up():
    values = {1: held("KKR", "parts differ from the total by 21.6%"), 2: held("GS", "lines differ from Management Fees by 3%"),
              3: held("APO", "the same figure as a year earlier: check the column")}
    assert picked_filings("sum-holds", values) == {"KKR", "GS"}


def test_failed_filings_picks_filings_that_failed_outright():
    filings = {"KKR-Q3-2018": {"status": "failed", "attempts": 4}, "DONE": {"status": "processed"}}
    assert picked_filings("failed-filings", {}, filings) == {"KKR-Q3-2018"}
    assert parse_args(["--reread", "failed-filings"]).reread == "failed-filings"


def test_place_holds_picks_figures_held_for_a_place_their_row_never_named():
    values = {1: held("KO", "the row and its headings name no: EMEA"),
              2: held("SUMS", "parts differ from the total by 4%"),
              3: held("DONE", "the row and its headings name no: EMEA", owner_decision="rejected")}
    assert picked_filings("place-holds", values) == {"KO"}


def test_reports_with_segment_income_but_no_segment_revenue_are_picked():
    from kpi_extractor.ops.reread import picked_filings
    values = {
        1: {"source_accession": "a", "method": "xbrl", "group_key": "segment_income"},
        2: {"source_accession": "b", "method": "xbrl", "group_key": "segment_income"},
        3: {"source_accession": "b", "method": "xbrl", "group_key": "segments"},
        4: {"source_accession": "c", "method": "ai", "group_key": "segment_income"},
    }
    assert picked_filings("reports-without-segments", values) == {"a"}


def test_figures_put_on_their_series_scale_by_code_are_read_again():
    from kpi_extractor.ops.reread import SELECTORS
    picks = SELECTORS["series-rescaled"][1]
    rescaled = {"validation_status": "verified", "notes": ["read without its table's scale: 1000× to match the series"]}
    assert picks(rescaled)
    assert not picks({**rescaled, "owner_decision": "corrected"}) and not picks({"validation_status": "verified", "notes": []})


def test_figures_a_reread_held_for_differing_are_read_once_more():
    from kpi_extractor.ops.reread import SELECTORS
    picks = SELECTORS["reread-differs"][1]
    held = {"validation_status": "needs_review",
            "notes": ["a new reading of this filing differs from the figure served before: 5,700,000,000"]}
    assert picks(held) and not picks({**held, "owner_decision": "confirmed"}) and not picks({**held, "validation_status": "verified"})


def test_figures_read_at_a_scale_the_ai_disputed_are_read_again():
    from kpi_extractor.ops.reread import SELECTORS
    picks = SELECTORS["scale-disputed"][1]
    assert picks({"validation_status": "verified", "notes": ["table declares scale 1e+06, AI said 1e+09"]})
    assert not picks({"validation_status": "verified", "notes": ["scale quoted: 'in billions' over the table's 1e+06"]})


def test_segment_pages_picks_company_metrics_held_for_a_segments_page():
    values = {
        "HELD": {"source_accession": "HELD", "validation_status": "needs_review",
                 "notes": ["read from a table headed for one segment: securities services"]},
        "DECIDED": {"source_accession": "DECIDED", "validation_status": "needs_review", "owner_decision": "accepted",
                    "notes": ["read from a table headed for one segment: securities services"]},
        "OTHER": {"source_accession": "OTHER", "validation_status": "needs_review", "notes": ["sharp change"]},
    }
    assert picked_filings("segment-pages", values) == {"HELD"}


def test_a_report_whose_quarter_a_split_skips_is_picked():
    from kpi_extractor.ops.reread import picked_filings
    def value(acc, group, end, kind="revenue_breakdown", period="Q1"):
        return {"source_accession": acc, "method": "xbrl", "group_key": group, "group_kind": kind, "period_end": end,
                "fiscal_period": period, "validation_status": "verified"}
    values = dict(enumerate([
        value("q4-17", "products", "2017-12-31", period="Q4"), value("q4-17", "segments", "2017-12-31", period="Q4"),
        value("q1-18", "segments", "2018-03-31"),  # Alphabet's Q1 2018 10-Q: segments, no products
        value("q2-19", "products", "2019-06-30", period="Q2"), value("q2-19", "products", "2018-06-30", period="Q2"),
        value("q2-19", "segments", "2019-06-30", period="Q2"),
        value("q3-19", "segments", "2019-09-30", period="Q3"),  # after the last products quarter: not a gap
    ]))
    assert picked_filings("reports-missing-split", values) == {"q1-18"}


def test_a_years_quarter_held_only_from_a_later_report_is_picked_when_its_q4_is_missing():
    from kpi_extractor.ops.reread import picked_filings
    def value(acc, end, year, period, group="products"):
        return {"source_accession": acc, "method": "xbrl", "group_key": group, "group_kind": "revenue_breakdown",
                "period_end": end, "fiscal_year": year, "fiscal_period": period, "validation_status": "verified"}
    rows = [value("10k-18", "2018-12-31", "2018", "FY"), value("q1-18", "2018-03-31", "2018", "Q1"),
            value("q2-18", "2018-06-30", "2018", "Q2", group="segments"),  # Alphabet's own Q2 2018 10-Q: no products
            value("q2-19", "2018-06-30", "2018", "Q2"), value("q2-19", "2019-06-30", "2019", "Q2"),
            value("q3-19", "2018-09-30", "2018", "Q3"), value("q3-19", "2019-09-30", "2019", "Q3")]
    assert "q2-18" in picked_filings("reports-missing-split", dict(enumerate(rows)))
    rows.append({**value("10k-18", "2018-12-31", "2018", "Q4"), "method": "derived"})  # once Q4 stands, nothing to read
    assert "q2-18" not in picked_filings("reports-missing-split", dict(enumerate(rows)))
