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
