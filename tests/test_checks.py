from types import SimpleNamespace as NS

from kpi_extractor.checks import READING_CHECKS, Figure, audit_stored, date_order_findings, findings, row_path


def _key(row):
    return (row["group_key"], row["kpi_key"], row["fiscal_year"], row["fiscal_period"])


def _row(key, value, year="2026", period="Q2", end="2026-06-30", method="xbrl", kind="revenue_breakdown", unit="currency", total=None, **fields):
    return {"group_key": "kpi_rev", "group_kind": kind, "kpi_key": key, "kpi_label": key.title(), "unit": unit, "value": value, "method": method,
            "validation_status": "verified", "fiscal_year": year, "fiscal_period": period, "period_end": end,
            "locator": {"total": total} if total else {}, **fields}


def _names(found):
    return [finding.check for finding in found]


def test_a_check_applies_only_to_what_it_declares():
    mix = {check.name: check for check in READING_CHECKS}["mix_adds_up"]
    assert mix.applies(Figure("g", "mix", "a", "percent", 1.0))
    assert not mix.applies(Figure("g", "metric", "a", "count", 1.0))
    assert not mix.applies(Figure("g", "mix", "a", "percent", 1.0, method="chart"))
    assert mix.applies(Figure("g", "mix", "a", "percent", 1.0, method="chart"), widened=True)


def test_the_engine_answers_and_changes_nothing():
    source = NS(status="verified", notes=[])
    figures = [Figure("g", "mix", "a", "percent", 60.0, source=source), Figure("g", "mix", "b", "percent", 60.0)]
    assert _names(findings(READING_CHECKS, figures, previous={})) == ["mix_adds_up"]
    assert source.status == "verified" and source.notes == []


def test_a_way_of_reading_no_check_covers_is_left_alone_until_widened():
    figures = [Figure("g", "revenue_breakdown", "a", "currency", 60.0, method="chart"),
               Figure("g", "revenue_breakdown", "b", "currency", 60.0, method="chart"),
               Figure("g", "revenue_breakdown", "t", "currency", 100.0, is_total=True, method="chart")]
    assert findings(READING_CHECKS, figures, previous={}) == []
    assert _names(findings(READING_CHECKS, figures, widened=True, previous={})) == ["breakdown_adds_up"]


def test_the_report_counts_a_parent_once_through_the_kpi_list():
    rows = [_row("fees", 50.0, method="ai", total=100.0), _row("fee_a", 30.0, method="ai", total=100.0),
            _row("fee_b", 20.0, method="ai", total=100.0), _row("interest", 50.0, method="ai", total=100.0)]
    assert [r["check"] for r in audit_stored(rows, _key)] != []
    parents = {("kpi_rev", "fee_a"): "fees", ("kpi_rev", "fee_b"): "fees"}
    assert audit_stored(rows, _key, parents) == []


def test_the_report_leaves_out_the_owners_figures_and_what_is_not_served():
    rows = [_row("a", 60.0, total=100.0, owner_decision="confirmed"), _row("b", 60.0, total=100.0),
            _row("c", 60.0, total=100.0, validation_status="needs_review")]
    found = audit_stored(rows, _key)
    assert {r["row"]["kpi_key"] for r in found} == {"b"} and "parts differ" in found[0]["note"]


def test_a_stored_period_missing_rows_is_found_whichever_way_it_was_read():
    # A year-ago row beside the same line under its later name counts the line twice.
    rows = [_row("ai", 737.0, total=4071.0, year_ago=True), _row("si", 737.0, total=4071.0), _row("space", 746.0, total=4071.0),
            _row("connectivity", 2588.0, total=4071.0)]
    assert {r["check"] for r in audit_stored(rows, _key)} == {"breakdown_adds_up"}


def test_date_order_judges_only_what_it_declares_unless_widened():
    rows = [_row("a", 100e6, end="2026-03-31", period="Q1"), _row("a", 900e6)]
    assert date_order_findings(rows, None, _key) == {}
    assert "changed 9.0x" in next(iter(date_order_findings(rows, None, _key, methods=None).values()))


def test_a_figure_says_how_it_was_read():
    assert row_path(_row("a", 1.0, locator={"tagged": True})) == "XBRL tag"
    assert row_path(_row("a", 1.0, method="chart")) == "chart"
