import ast
import glob
from datetime import date
from types import SimpleNamespace

import pytest

from kpi_extractor.sources.sec import FilingRef
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


def test_a_held_figure_flagged_and_stood_back_is_not_sent():
    # One step flags a breakdown line, a later one stands it back: the run's net change is none, so nothing is stored
    # (each was a dated version lasting seconds, KKR's 835 in a day).
    control = Control()
    store = _store(control)
    store.load([_row("a", notes=["confirmed by review"]), _row("b", value=5.0)])
    store.hold()
    store.write([_row("a", status="needs_review", notes=["parts differ from the total by 23.2%"])])
    store.write([_row("a", notes=["confirmed by review"])])
    store.write([_row("b", value=6.0, status="needs_review")])
    store.write([_row("b", value=6.0)])
    assert control.calls == []  # nothing goes out while held
    assert store.values[("kpi_x", "a", "2026", "Q2")]["validation_status"] == "verified"  # steps see each other's rows
    store.release()
    sent = [value for _operation, payload in control.calls for value in payload["values"]]
    assert [(value["kpi_key"], value["value"], value["validation_status"]) for value in sent] == [("b", 6.0, "verified")]
    store.release()  # a second release has nothing left
    assert len(control.calls) == 1


def test_a_reading_recorded_while_held_goes_out_at_once():
    control = Control()
    store = _store(control)
    store.hold()
    store.record(FilingReading(_ref(), "processed", records=[_row("a")]))
    assert len(control.calls) == 1 and control.calls[0][1]["filings"][0]["accession"] == "f1"
    store.release()
    assert len(control.calls) == 1  # the recorded row is not sent again


def test_a_new_reading_that_changes_a_verified_figure_of_its_filing_goes_to_review():
    # The 2026-11-09 retrace: Salesforce's cRPO re-read as its total RPO ($12.8B → $25.9B), still verified.
    store = _store()
    store.load([_row("crpo", value=12_800_000_000.0), _row("same", value=5.0), _row("rounded", value=6_559.1),
                _row("other_filing", value=3.0), _row("owner", value=7.0, owner_decision="confirmed"),
                _row("flagged", value=1.0, status="needs_review")])
    store.write([_row("crpo", value=25_900_000_000.0), _row("same", value=5.0), _row("rounded", value=6_559.0),
                 _row("other_filing", value=4.0, accession="f2"), _row("flagged", value=2.0)])
    held = {row["kpi_key"]: row for row in store.reread_disagreements()}
    assert list(held) == ["crpo"]
    assert held["crpo"]["validation_status"] == "needs_review" and held["crpo"]["value"] == 25_900_000_000.0
    assert held["crpo"]["notes"][-1] == "a new reading of this filing differs from the figure served before: 12,800,000,000"


def _breakdown(key, value, group="products", accession="f1", **fields):
    return _row(key, value=value, accession=accession, group_key=group, group_kind="revenue_breakdown",
                group_label="Net sales by category", group_order=1, kpi_label=key.title(), **fields)


def test_a_re_read_keeps_the_group_its_earlier_reading_used():
    # Apple: its 2018 release lines were read under products; the KPI list since names the breakdown anew.
    store = _store()
    store.load([_breakdown("iphone", 61.0), _breakdown("mac", 6.8)], [{"accession": "f1", "status": "processed"}])
    renamed = [_breakdown("iphone", 61.0, group="kpi_revenue_by_product_type"),
               _breakdown("mac", 6.8, group="kpi_revenue_by_product_type")]
    store.record(FilingReading(_ref(), "processed", records=renamed))
    kept = {key: row for key, row in store.values.items() if row["validation_status"] == "verified"}
    assert {key[0] for key in kept} == {"products"} and len(kept) == 2
    assert all(row["group_label"] == "Net sales by category" for row in kept.values())


def test_a_re_read_that_reads_other_figures_takes_its_own_group():
    store = _store()
    store.load([_breakdown("iphone", 61.0), _breakdown("mac", 6.8)], [{"accession": "f1", "status": "processed"}])
    store.record(FilingReading(_ref(), "processed", records=[_breakdown("iphone", 70.0, group="kpi_new")]))
    assert store.values[("kpi_new", "iphone", "2026", "Q2")]["validation_status"] == "verified"
    assert store.values[("products", "iphone", "2026", "Q2")]["validation_status"] == "rejected"


STATED = {"row_label": "Capital invested", "row_numbers": [9833], "scale": 1e6}


def test_a_re_read_keeps_a_verified_figure_its_filing_states_that_the_new_reading_missed():
    # KKR's Q4 2019 capital invested, $9.8B, read from its named row; a re-read on 2026-11-09 did not produce it.
    store = _store()
    store.windows = {("kpi_x", "capital_invested"): (None, None), ("kpi_x", "other"): (None, None)}
    store.load([_row("capital_invested", value=9_833e6, unit="currency", kpi_label="Capital Invested", locator=STATED),
                _row("other", value=5.0, unit="currency", kpi_label="Other", locator={})],
               [{"accession": "f1", "status": "processed"}])
    store.record(FilingReading(_ref(), "processed", records=[_row("fresh", value=1.0)]))
    assert store.values[("kpi_x", "capital_invested", "2026", "Q2")]["validation_status"] == "verified"
    assert store.values[("kpi_x", "other", "2026", "Q2")]["validation_status"] == "rejected"  # nothing shows it


def test_a_figure_read_again_elsewhere_or_dropped_from_the_list_is_replaced():
    store = _store()
    store.windows = {("kpi_x", "renamed"): (None, None)}
    store.load([_row("capital_invested", value=9_833e6, unit="currency", kpi_label="Capital Invested", locator=STATED)],
               [{"accession": "f1", "status": "processed"}])
    store.record(FilingReading(_ref(), "processed", records=[_row("renamed", value=9_833e6)]))
    assert store.values[("kpi_x", "capital_invested", "2026", "Q2")]["validation_status"] == "rejected"


def test_a_series_the_filing_also_fed_keeps_its_figures_when_a_re_read_feeds_only_the_other():
    # Apple's 2016-2018 releases fed both its AI breakdown and the XBRL products series; a re-read fed only the first.
    store = _store()
    store.windows = {("kpi_revenue_by_product_type", "iphone"): (None, None), ("products", "iphone"): (None, None)}
    stated = {"row_label": "iPhone", "row_numbers": [61.0], "scale": 1}
    store.load([_breakdown("iphone", 61.0, locator=stated, unit="currency"),
                _breakdown("iphone", 61.0, group="kpi_revenue_by_product_type", locator=stated, unit="currency")],
               [{"accession": "f1", "status": "processed"}])
    store.record(FilingReading(_ref(), "processed", records=[
        _breakdown("iphone", 61.0, group="kpi_revenue_by_product_type", locator=stated, unit="currency")]))
    assert store.values[("products", "iphone", "2026", "Q2")]["validation_status"] == "verified"
    assert store.values[("kpi_revenue_by_product_type", "iphone", "2026", "Q2")]["validation_status"] == "verified"
