from kpi_extractor.evals.code_reads import CodeOnlyControl, held_out_companies, origin, score_company, summarize, report_text
from kpi_extractor.steps.shared import value_key


def _value(kpi, value, status="verified", accession="a1", period=("2026", "Q2"), locator=None, method="ai"):
    return {"group_key": "kpi_users", "kpi_key": kpi, "fiscal_year": int(period[0]), "fiscal_period": period[1],
            "period_end": f"{period[0]}-06-30", "value": value, "validation_status": status, "method": method,
            "source_accession": accession, "group_label": "Users", "kpi_label": kpi, "locator": locator or {"ai_call": "c1"}}


def test_each_verified_release_figure_is_scored_against_code_reading_of_its_filing():
    state = [_value("dau", 3.6), _value("mau", 4.1), _value("arpu", 12.0), _value("paid", 7.0), _value("cities", 10.0),
             _value("orders", 5.0, status="needs_review")]
    read = [_value("dau", 3.6, locator={"replayed_from": "a0"}), _value("mau", 4.2), _value("arpu", 12.0, status="needs_review"),
            _value("cities", 10.04), _value("orders", 5.0, locator={"quarter_table": True})]
    rows = score_company("META", state, {"users": "metric"}, {"a1"}, {value_key(row): row for row in read}, held_out=False)
    outcomes = {row["kpi"]: row["outcome"] for row in rows}
    assert outcomes == {"dau": "right", "mau": "wrong", "arpu": "held", "paid": "missed", "cities": "rounded", "orders": "extra"}
    assert {row["kind"] for row in rows} == {"metric"}
    assert {row["kpi"]: row["readBy"] for row in rows if row["readBy"]} == {"dau": "replay", "mau": "other", "cities": "other",
                                                                            "orders": "quarter_table"}


def test_the_latest_four_quarters_and_hashed_companies_are_held_out():
    periods = [("2025", "Q1"), ("2025", "Q2"), ("2025", "Q3"), ("2025", "Q4"), ("2026", "Q1")]
    state = [{**_value("dau", 1.0, period=period), "period_end": f"{period[0]}-{3 * int(period[1][1]):02d}-28"} for period in periods]
    rows = score_company("META", state, {}, {"a1"}, {}, held_out=False)
    assert sorted((row["period"], row["set"]) for row in rows)[0] == ("2025-Q1", "tuning")
    assert sum(row["set"] == "held out" for row in rows) == 4
    assert held_out_companies(["A", "B", "C", "D", "E", "F", "G"]) == held_out_companies(["G", "F", "E", "D", "C", "B", "A"])
    assert len(held_out_companies(["A", "B", "C", "D", "E", "F", "G"])) == 5


def test_origin_names_what_read_the_stored_figure():
    assert origin({"locator": {"ai_call": "c"}}) == "ai"
    assert origin({"locator": {"replayed_from": "a0"}}) == "replay"
    assert origin({"locator": {"quarter_table": True}}) == "code"
    assert origin({"locator": {}, "owner_decision": "kept"}) == "owner"


def test_ai_requests_are_refused_and_writes_kept_local():
    class Plane:
        run_id = "r"
        def call(self, operation, **payload):
            raise AssertionError(f"{operation} reached the control plane")
    control = CodeOnlyControl(Plane())
    assert control.ai("META", "locate", "system", "user", False) == ""
    assert control.call("store", symbol="META", values=[{}]) == {}
    assert control.refused == {"locate": 1}


def test_report_shows_coverage_speed_and_buckets():
    rows = score_company("META", [_value("dau", 3.6), _value("mau", 4.1)], {}, {"a1"}, {value_key(_value("dau", 3.6)): _value("dau", 3.6)}, held_out=True)
    summary = summarize([{"symbol": "META", "rows": rows, "outcomes": {"row label not found": 1}, "left": {}, "refused": {},
                          "errors": [], "codeSeconds": [0.01, 0.03], "seconds": 2.0}])
    text = report_text(summary)
    assert "| held out · operating metrics | 2 | 1 | 0 | 0 | 0 | 1 | 50.0% | 0 |" in text
    assert "row label not found" in text and "ms median" in text



from types import SimpleNamespace

from kpi_extractor.documents.document import parse_document
from kpi_extractor.evals.code_reads import CodeOnlyPipeline
from tests.readers.test_replay import Q2, SPEC, URL, _history, report


class _NoPlane:
    run_id = "r"

    def call(self, operation, **payload):
        return {}


def _code_only(values):
    pipeline = CodeOnlyPipeline(CodeOnlyControl(_NoPlane()), "TSM", 4, log=lambda *_: None)
    pipeline.profile = SimpleNamespace(name="TSMC", fiscal_year_end="1231", foreign=True)
    pipeline.values = {(v["group_key"], v["kpi_key"], v["fiscal_year"], v["fiscal_period"]): v for v in values}
    pipeline.filings = {"q1": {"accession": "q1", "spec_version": 1, "status": "processed", "notes": ["read by AI"]}}
    return pipeline


def test_code_reads_a_quarter_from_the_places_its_earlier_quarter_was_verified_at_with_no_ai():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    pipeline = _code_only(_history(first))
    read, problems, _located = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert {item.kpi.key: item.value for item in read if not item.is_total}["members"] == 310_000_000
    assert not problems and pipeline.control.refused == {} and len(pipeline.timings) == 1 and not pipeline.left


def test_what_code_cannot_read_is_left_unread_and_no_later_quarter_lends_its_places():
    first = parse_document(report("March 31", 10, 20, 30, 40, 300), URL.format(n=1))
    six = parse_document(report("June 30", 21, 45, 61, 82, 310).replace("Three Months Ended", "Six Months Ended"), URL.format(n=2))
    pipeline = _code_only(_history(first))
    read, _problems, _located = pipeline._read(lambda: "prompt", six, SPEC, Q2, version=1)
    assert {item.kpi.key for item in read} == {"members"} and pipeline.left == {"technology": 1}
    # A whole table's miss is each of its rows' (the table was read whole last quarter).
    assert {miss["why"] for miss in pipeline.misses.values()} == {"column header changed"}
    assert "technology.n3" in {key for _accession, key in pipeline.misses}
    assert pipeline.control.refused == {}
    later = [{**value, "period_end": "2026-09-30", "fiscal_period": "Q3"} for value in _history(first)]
    pipeline = _code_only(later)
    second = parse_document(report("June 30", 11, 25, 31, 42, 310), URL.format(n=2))
    read, _problems, _located = pipeline._read(lambda: "prompt", second, SPEC, Q2, version=1)
    assert read == [] and pipeline.result.replay_outcomes == {"no earlier reading of this kind of document": 2}
    assert {miss["why"] for miss in pipeline.misses.values()} == {"no earlier reading of this kind of document"}


def test_after_the_releases_the_tested_filings_hold_only_what_code_read_and_the_store_is_live_again():
    stored = [_value("dau", 3.6), _value("mau", 4.1), _value("paid", 7.0, accession="other")]
    pipeline = _code_only(stored)
    pipeline.store.load(stored, [])
    pipeline.store._apply = lambda values: None  # frozen while the releases are read
    pipeline._reread_picked = {"a1"}
    pipeline.readings = {"a1": [_value("dau", 3.6, locator={"replayed_from": "a0"})]}
    pipeline._code_only_state()
    assert {key[1] for key in pipeline.store._values} == {"dau", "paid"}  # mau was the AI's, so it is gone
    pipeline.store._apply([_value("mau", 4.1, locator={"quarter_table": True})])  # a fill step's write lands
    assert pipeline.store._values[value_key(_value("mau", 4.1))]["locator"] == {"quarter_table": True}
