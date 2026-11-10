"""Every figure read from a filing traces to what read it: the AI call that located it, or for a replayed figure, the
filing whose reading placed it and the AI call that first found that place."""
from types import SimpleNamespace

from kpi_extractor.readers.trace import stamp_call, trace_replay


def _item(group, kpi):
    return SimpleNamespace(group=SimpleNamespace(key=group), kpi=SimpleNamespace(key=kpi), locator={"table": "A_T3", "row": 2})


def test_an_ai_reading_carries_its_call_and_a_missing_call_leaves_nothing():
    read = [_item("revenue", "wafers")]
    stamp_call(read, None)
    assert "ai_call" not in read[0].locator
    stamp_call(read, "call-17")
    assert read[0].locator["ai_call"] == "call-17"


def test_a_replayed_figure_names_the_filing_it_came_from_and_the_call_that_first_placed_it():
    # TSMC's wafer revenue replayed from last quarter's release, whose reading was itself replayed from an AI read.
    read = [_item("revenue", "wafers"), _item("revenue", "n3")]
    earlier = {"revenue.wafers": {"table": "A_T3", "row": 2, "placed_by_call": "call-9"},
               "revenue.other": {"table": "A_T3", "row": 5, "ai_call": "call-12"}}
    history = {"revenue.wafers": {"source_accession": "0001046179-26-000451"}}
    trace_replay(read, earlier, history, "0001046179-26-000400")
    assert read[0].locator["replayed_from"] == "0001046179-26-000451" and read[0].locator["placed_by_call"] == "call-9"
    # A row the whole table gained since last quarter: its table's reading.
    assert read[1].locator["replayed_from"] == "0001046179-26-000400" and read[1].locator["placed_by_call"] == "call-9"


def test_a_replay_of_a_reading_from_before_tracing_names_its_filing_only():
    read = [_item("revenue", "wafers")]
    trace_replay(read, {"revenue.wafers": {"table": "A_T3"}}, {}, "0001046179-25-000100")
    assert read[0].locator == {"table": "A_T3", "row": 2, "replayed_from": "0001046179-25-000100"}


def test_a_figure_is_untraced_only_when_nothing_names_what_read_it():
    from kpi_extractor.readers.trace import untraced
    old = {"method": "ai", "validation_status": "verified", "locator": {"table": "A_T3", "row": 2}}
    assert untraced(old)
    assert not untraced({**old, "locator": {"table": "A_T3", "ai_call": "c1"}})
    assert not untraced({**old, "locator": {"table": "A_T3", "replayed_from": "0001"}})
    assert not untraced({**old, "locator": {"quoted_row": True}})
    assert not untraced({**old, "owner_decision": "corrected"})  # the owner's decision is its trace
    assert not untraced({**old, "method": "xbrl"}) and not untraced({**old, "validation_status": "rejected"})


def test_a_retrace_run_reads_again_only_filings_holding_untraced_figures():
    from types import SimpleNamespace
    from kpi_extractor.steps.loading import FilingLoading
    values = {1: {"method": "ai", "validation_status": "verified", "source_accession": "OLD", "locator": {"table": "T"}},
              2: {"method": "ai", "validation_status": "verified", "source_accession": "NEW",
                  "locator": {"table": "T", "ai_call": "c9"}}}
    run = SimpleNamespace(values=values, reread="untraced", log=lambda *_: None)
    assert FilingLoading._reread_due(run, "OLD") and not FilingLoading._reread_due(run, "NEW")
    # An ordinary run never re-reads for this.
    assert not FilingLoading._reread_due(SimpleNamespace(values=values, reread=None), "OLD")
