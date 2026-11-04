import ast
import glob
from datetime import date
from types import SimpleNamespace

import pytest

from kpi_extractor.sec import FilingRef
from kpi_extractor.store import CompanyStore, FilingReading, failed_reading

# The only step files that may change rows or filings themselves: the KPI list changes its rows with the list, and the
# release flow records each reading while it holds the lock its readers share. Every other step hands its rows back.
_MAY_WRITE = {"kpi_list.py", "release_flow.py"}
_ROW_WRITES = {"write", "record", "record_or_fail", "carry"}


def _row(key="a", status="verified", accession="f1", method="ai", **fields):
    return {"group_key": "kpi_x", "kpi_key": key, "fiscal_year": "2026", "fiscal_period": "Q2", "value": 1.0, "method": method,
            "validation_status": status, "source_accession": accession, "source_url": "https://www.sec.gov/x.htm", **fields}


def _ref(accession="f1"):
    return FilingRef(accession, "8-K", date(2026, 7, 1), "earnings_release", "https://www.sec.gov/x.htm")


class Control:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def call(self, operation, **payload):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("store failed")
        self.calls.append((operation, payload))
        return {}


def _store(control=None):
    result = SimpleNamespace(values=0, filings=0, needs_review=0, errors=[])
    return CompanyStore(control or Control(), "X", result, log=lambda *_: None)


def test_stored_rows_and_filings_cannot_be_changed_from_outside():
    store = _store()
    store.load([_row()], [{"accession": "f1", "status": "processed"}])
    row = store.values[("kpi_x", "a", "2026", "Q2")]
    with pytest.raises(TypeError):
        store.values[("kpi_x", "b", "2026", "Q2")] = _row("b")
    with pytest.raises(TypeError):
        row["value"] = 2.0
    with pytest.raises(TypeError):
        store.filings["f2"] = {}
    assert {**row, "value": 2.0}["value"] == 2.0 and row["value"] == 1.0


def test_a_stored_row_handed_back_goes_out_as_a_plain_record():
    control = Control()
    store = _store(control)
    store.load([_row()])
    store.write([store.values[("kpi_x", "a", "2026", "Q2")]])
    assert type(control.calls[0][1]["values"][0]) is dict


def test_a_long_write_goes_in_batches_and_nothing_empty_goes_at_all():
    control = Control()
    store = _store(control)
    store.write([])
    assert control.calls == []
    store.write([_row(str(number)) for number in range(401)])
    assert [len(payload["values"]) for _, payload in control.calls] == [400, 1]
    assert store.result.values == 401 and len(store.values) == 401


def test_a_filing_and_its_rows_go_in_one_call():
    control = Control()
    store = _store(control)
    store.record(FilingReading(_ref(), "processed", records=[_row(str(number)) for number in range(500)]))
    assert len(control.calls) == 1 and len(control.calls[0][1]["values"]) == 500
    assert store.filings["f1"]["value_count"] == 500 and store.result.filings == 1


def test_a_reading_that_cannot_be_stored_marks_its_filing_failed():
    control = Control(failures=1)
    store = _store(control)
    store.load([_row("kept")], [{"accession": "f1", "status": "processed", "attempts": 1}])
    store.record_or_fail(FilingReading(_ref(), "processed", records=[_row("new")]))
    assert store.filings["f1"]["status"] == "failed" and store.filings["f1"]["attempts"] == 2
    assert store.result.errors == ["f1: RuntimeError: store failed"]
    assert store.values[("kpi_x", "kept", "2026", "Q2")]["validation_status"] == "verified"
    assert ("kpi_x", "new", "2026", "Q2") not in store.values


def test_a_failure_that_cannot_be_stored_either_stops_the_company():
    store = _store(Control(failures=1))
    with pytest.raises(RuntimeError):
        store.record_or_fail(failed_reading(_ref(), ValueError("bad filing")))


def test_only_the_store_talks_to_the_control_plane_about_figures():
    for path in [*glob.glob("kpi_extractor/steps/*.py"), "kpi_extractor/pipeline.py"]:
        for node in ast.walk(ast.parse(open(path).read())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "call" and node.args:
                name = getattr(node.args[0], "value", "")
                assert not str(name).startswith("store"), f"{path}:{node.lineno} writes around the store"


def test_steps_hand_their_rows_back_instead_of_writing_them():
    for path in glob.glob("kpi_extractor/steps/*.py"):
        if path.rsplit("/", 1)[-1] in _MAY_WRITE:
            continue
        for node in ast.walk(ast.parse(open(path).read())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _ROW_WRITES
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "store"):
                assert False, f"{path}:{node.lineno} writes rows itself"


def test_what_the_owner_decided_stands_here_as_it_does_in_the_control_plane():
    store = _store()
    store.load([_row("kept", owner_decision="confirmed")], [], [{"group_key": "kpi_x", "kpi_key": "gone", "fiscal_year": "2026", "fiscal_period": "Q2"}])
    store.write([{**_row("kept"), "validation_status": "rejected"}, _row("gone"), _row("new")])
    assert store.values[("kpi_x", "kept", "2026", "Q2")]["validation_status"] == "verified"
    assert ("kpi_x", "gone", "2026", "Q2") not in store.values and ("kpi_x", "new", "2026", "Q2") in store.values
    assert all("owner_decision" not in row for row in store.control.calls[0][1]["values"])


def test_a_re_read_keeps_a_period_the_owner_confirmed_held():
    # SpaceX: its Q2 2026 report re-read must not empty Q2 2025, or its year-ago lines come back under an old name.
    store = _store()
    store.load([_row("si", owner_decision="confirmed", method="xbrl", accession="f1")], [{"accession": "f1", "status": "processed"}])
    reading = FilingReading(FilingRef("f1", "10-Q", date(2026, 8, 4), "periodic_report", "https://www.sec.gov/x.htm"), "processed", records=[])
    store.record(reading)
    assert store.values[("kpi_x", "si", "2026", "Q2")]["validation_status"] == "verified"


def test_a_run_says_what_it_changed_among_the_figures_served():
    store = _store()
    store.load([_row("same"), _row("moved"), _row("gone"), _row("flagged", status="needs_review"), _row("named", kpi_label="Old")])
    assert store.served_changes() == {}
    store.write([{**_row("moved"), "value": 2.0}, {**_row("gone"), "validation_status": "rejected"}, _row("new"), _row("flagged"),
                 _row("named", kpi_label="New")])
    changes = store.served_changes()
    assert {key: changes[key] for key in ("added", "removed", "changed", "renamed")} == {"added": 2, "removed": 1, "changed": 1, "renamed": 1}
    assert {"change": "changed", "group": "kpi_x", "kpi": "moved", "year": "2026", "period": "Q2"} in changes["examples"]
