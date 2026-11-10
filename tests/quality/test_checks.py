from types import SimpleNamespace as NS

from kpi_extractor.quality.checks import READING_CHECKS, Figure, audit_stored, date_order_findings, findings, parts_add_up, row_path


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


def test_two_quarters_apart_is_not_a_change_in_a_quarter():
    rows = [_row("a", 100e6, end="2025-12-31", period="Q4", year="2025"), _row("a", 900e6)]
    assert date_order_findings(rows, None, _key, methods=None) == {}


def test_parts_add_up_counts_a_parent_once_and_needs_a_stated_total():
    rows = [_row("fees", 50.0, total=100.0), _row("fee_a", 50.0, total=100.0), _row("interest", 50.0, total=100.0)]
    assert not parts_add_up(rows)
    assert parts_add_up(rows, {("kpi_rev", "fee_a"): "fees"})
    assert parts_add_up([_row("a", 1.0), _row("b", 2.0)])


def test_a_smaller_breakdown_read_from_the_tables_own_total_is_the_whole_table():
    from kpi_extractor.quality.checks import looks_like_part_of_table
    # TSMC's 2nm and 3nm under "advanced technologies": fewer rows, a subtotal for the total, held.
    assert looks_like_part_of_table(2, 40, 6, 100, "Advanced technologies")
    # The same shrink read from the row the table calls its total stands: size alone is no reason.
    assert not looks_like_part_of_table(2, 40, 6, 100, "Total net revenue")
    assert not looks_like_part_of_table(2, 40, 6, 100, "Net sales")


def test_a_repeat_is_held_only_when_its_source_shows_another_period():
    # APO's fee-generating credit AUM: $135 billion in Q3 2025 and again in Q3 2026, read from a chart of the quarter alone.
    def pair(locator):
        return [_row("credit", 135e9, year="2025", period="Q3", end="2025-09-30", method="ai"),
                _row("credit", 135e9, year="2026", period="Q3", end="2026-09-30", method="ai", locator=locator)]
    assert date_order_findings(pair({"quote": "$135 $85 Credit Equity"}), None, _key) == {}
    assert date_order_findings(pair({"quote": "Uncalled Commitments of $135 billion, up 3% year-over-year"}), None, _key) == {}
    # A quote that sets an earlier period beside it, or a row printing two figures, may have given the earlier one.
    held = "the same figure as a year earlier: check the column"
    assert held in date_order_findings(pair({"quote": "AUM of $140 billion, up from $135 billion a year ago"}), None, _key).values()
    assert held in date_order_findings(pair({"quote": "Q3 2026 $140 Q3 2025 $135"}), None, _key).values()
    assert held in date_order_findings(pair({"row_numbers": [135, 140]}), None, _key).values()
    # A row with one figure states one period; one printing the same figure twice is the company's own repeat.
    assert date_order_findings(pair({"row_numbers": [135]}), None, _key) == {}
    assert date_order_findings(pair({"row_numbers": [135, 135]}), None, _key) == {}


def test_a_cell_read_twice_counts_once():
    # UnitedHealthcare's Employer & Individual row read as the list's Domestic and again under its own name: counted
    # twice, the parts overshot the total by 36%.
    cell = {"table": "A_T8", "row": 16, "col": 1, "total": 37_556.0}
    rows = [_row("employer_individual_domestic", 13_509.0, locator=dict(cell), source_accession="X"),
            _row("unitedhealthcareemployerandindividual", 13_509.0, locator=dict(cell), source_accession="X"),
            _row("employer_individual_global", 1_490.0, locator={**cell, "row": 17}, source_accession="X"),
            _row("medicare_retirement", 13_000.0, locator={**cell, "row": 18}, source_accession="X"),
            _row("community_state", 9_557.0, locator={**cell, "row": 19}, source_accession="X")]
    assert parts_add_up(rows, {("kpi_rev", "employer_individual_domestic"): None, ("kpi_rev", "employer_individual_global"): None,
                               ("kpi_rev", "medicare_retirement"): None, ("kpi_rev", "community_state"): None})
    # The same figure from another filing's same cell is another reading, not a copy.
    other = [dict(row) for row in rows]
    other[1]["source_accession"] = "Y"
    assert not parts_add_up(other)
