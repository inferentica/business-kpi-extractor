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
