import pytest
from kpi_extractor.derive import derive_periods


def _value(period, kpi, value, *, method="xbrl", group="segments", status="verified", year="2026"):
    return {"symbol": "X", "group_key": group, "group_kind": "revenue_breakdown", "kpi_key": kpi, "fiscal_year": year,
            "fiscal_period": period, "period_end": {"Q1": "2026-03-31", "Q2": "2026-06-30", "Q3": "2026-09-30",
                                                     "Q4": "2026-12-31", "FY": "2026-12-31"}[period],
            "value": value, "method": method, "validation_status": status, "notes": [], "locator": None}


def test_q4_is_the_year_minus_three_quarters():
    values = [_value("FY", "a", 100), _value("FY", "b", 40)]
    for period, a, b in (("Q1", 20, 10), ("Q2", 25, 10), ("Q3", 30, 10)):
        values += [_value(period, "a", a), _value(period, "b", b)]
    derived = {(row["fiscal_period"], row["kpi_key"]): row for row in derive_periods(values)}
    assert derived[("Q4", "a")]["value"] == 25
    assert derived[("Q4", "b")]["value"] == 10
    assert derived[("Q4", "a")]["method"] == "derived"
    assert derived[("Q4", "a")]["validation_status"] == "verified"


def test_no_q4_when_segments_were_renamed_mid_year():
    values = [_value("FY", "a", 100), _value("FY", "b", 40)]
    values += [_value("Q1", "a", 20), _value("Q1", "c", 10), _value("Q2", "a", 25), _value("Q2", "b", 10),
               _value("Q3", "a", 30), _value("Q3", "b", 10)]
    assert derive_periods(values) == []


def test_negative_q4_needs_review():
    values = [_value("FY", "a", 50)]
    values += [_value(period, "a", 20) for period in ("Q1", "Q2", "Q3")]
    [q4] = derive_periods(values)
    assert q4["value"] == -10
    assert q4["validation_status"] == "needs_review"


def test_full_year_from_four_release_quarters():
    values = [_value(period, "a", 10, method="ai", group="kpi_segments") for period in ("Q1", "Q2", "Q3", "Q4")]
    [annual] = derive_periods(values)
    assert annual["fiscal_period"] == "FY"
    assert annual["value"] == 40
    assert annual["period_end"] == "2026-12-31"


def test_derived_periods_reconcile_to_the_carried_total():
    values = [_value("FY", "a", 100) | {"locator": {"total": 140}}, _value("FY", "b", 40) | {"locator": {"total": 140}}]
    for period, a, b in (("Q1", 20, 10), ("Q2", 25, 10), ("Q3", 30, 10)):
        values += [_value(period, "a", a) | {"locator": {"total": a + b}}, _value(period, "b", b) | {"locator": {"total": a + b}}]
    derived = derive_periods(values)
    assert {row["locator"]["total"] for row in derived} == {35}
    assert all(row["reconciliation_error_pct"] == 0 for row in derived)


def test_q4_from_an_annual_report_read_by_the_ai():
    values = [_value("FY", "hpc", 100, method="ai", group="kpi_platform")]
    values += [_value(period, "hpc", 20, method="ai", group="kpi_platform") for period in ("Q1", "Q2", "Q3")]
    [q4] = derive_periods(values)
    assert (q4["fiscal_period"], q4["value"]) == ("Q4", 40)


def test_flagged_partial_readings_neither_block_nor_enter_a_derived_q4():
    values = [_value("FY", "a", 100, method="ai"), _value("FY", "b", 40, method="ai")]
    for period, a, b in (("Q1", 20, 10), ("Q2", 25, 10), ("Q3", 30, 10)):
        values += [_value(period, "a", a, method="ai"), _value(period, "b", b, method="ai"),
                   _value(period, "n3", 5, method="ai", status="needs_review")]
    values.append(_value("Q4", "n3", 6, method="ai", status="needs_review"))
    derived = {row["kpi_key"]: row["value"] for row in derive_periods(values) if row["fiscal_period"] == "Q4"}
    assert derived == {"a": 25, "b": 10}


def test_q4_is_the_year_less_the_third_quarters_nine_months():
    values = [_value("FY", "a", 100, method="xbrl"), _value("FY", "b", 40, method="xbrl")]
    for row in values:
        row["locator"] = {"total": 140}
    # Q1 was restated after its report, so its old figures no longer add up with the year; the nine months do.
    values += [_value("Q1", "a", 99), _value("Q1", "b", 1), _value("Q2", "a", 25), _value("Q2", "b", 10)]
    third = [_value("Q3", "a", 30), _value("Q3", "b", 10)]
    third[0]["locator"] = {"total": 40, "ytd": 70, "ytd_total": 100}
    third[1]["locator"] = {"total": 40, "ytd": 30, "ytd_total": 100}
    derived = {row["kpi_key"]: row for row in derive_periods(values + third) if row["fiscal_period"] == "Q4"}
    assert derived["a"]["value"] == 30 and derived["b"]["value"] == 10
    assert derived["a"]["locator"] == {"total": 40} and derived["a"]["reconciliation_error_pct"] == 0


def test_quarters_are_laid_out_like_their_year():
    values = [_value("FY", "us", 100), _value("FY", "other", 60)]
    for period in ("Q1", "Q2", "Q3"):
        values += [_value(period, "us", 20), _value(period, "other", 5), _value(period, "europe", 7),
                   _value(period, "tiny", 0.1)]
    derived = {row["kpi_key"]: row["value"] for row in derive_periods(values) if row["fiscal_period"] == "Q4"}
    assert derived == {"us": 40, "other": pytest.approx(60 - 3 * 12.1)}


def test_a_derived_q4_that_does_not_reconcile_is_not_verified():
    values = [_value("FY", "a", 120, method="xbrl"), _value("FY", "b", 30, method="xbrl")]
    for row in values:
        row["locator"] = {"total": 150}
    third = [_value("Q3", "a", 30), _value("Q3", "b", 10)]
    third[0]["locator"] = {"total": 40, "ytd": 30, "ytd_total": 100}  # a nine-month figure that lost a merged row
    third[1]["locator"] = {"total": 40, "ytd": 10, "ytd_total": 100}
    derived = [row for row in derive_periods(values + third) if row["fiscal_period"] == "Q4"]
    assert derived and all(row["validation_status"] == "needs_review" for row in derived)


def test_a_recast_year_takes_its_nine_months_from_next_years_third_quarter():
    values = [_value("FY", "a", 100, method="xbrl", year="2023"), _value("FY", "b", 60, method="xbrl", year="2023"),
              _value("FY", "c", 40, method="xbrl", year="2023")]
    for row in values:
        row["locator"] = {"total": 200}
    old = [_value("Q3", "a", 30, year="2023"), _value("Q3", "b", 30, year="2023")]  # before the recast: no "c"
    for row in old:
        row["locator"] = {"total": 60, "ytd": 70, "ytd_total": 150}
    later = [_value("Q3", k, 1, year="2024") for k in ("a", "b", "c")]
    for row, prior in zip(later, (70, 45, 35)):
        row["locator"] = {"total": 3, "prior_ytd": prior, "prior_ytd_total": 150}
    derived = {r["kpi_key"]: r["value"] for r in derive_periods(values + old + later) if r["fiscal_period"] == "Q4" and r["fiscal_year"] == "2023"}
    assert derived == {"a": 30, "b": 15, "c": 5}


def test_a_layout_no_quarter_used_is_derived_on_next_years_restated_basis():
    year = [_value("FY", k, v, method="xbrl", year="2023") for k, v in (("a", 100), ("b", 60), ("d", 40))]
    for row in year:
        row["locator"] = {"total": 200}
    following = [_value("FY", k, v, method="xbrl", year="2024") for k, v in (("a", 110), ("e", 120))]
    for row, prior in zip(following, (95, 105)):
        row["locator"] = {"total": 230, "prior": prior}
    third = [_value("Q3", k, 1, year="2024") for k in ("a", "e")]
    for row, prior in zip(third, (70, 80)):
        row["locator"] = {"total": 2, "prior_ytd": prior, "prior_ytd_total": 150}
    derived = {r["kpi_key"]: r["value"] for r in derive_periods(year + following + third)
               if r["fiscal_period"] == "Q4" and r["fiscal_year"] == "2023"}
    assert derived == {"a": 25, "e": 25}


def test_a_q4_that_one_source_leaves_negative_is_derived_from_the_next():
    year = [_value("FY", "products", 44.8, method="xbrl", year="2025"), _value("FY", "services", 19.0, method="xbrl", year="2025")]
    for row in year:
        row["locator"] = {"total": 63.8}
    third = [_value("Q3", "products", 9.3, year="2025"), _value("Q3", "services", 6.7, year="2025")]
    third[0]["locator"] = {"total": 16.0, "ytd": 25.9, "ytd_total": 45.8}
    third[1]["locator"] = {"total": 16.0, "ytd": 19.9, "ytd_total": 45.8}  # more than the year: reclassified later
    later = [_value("Q3", "products", 1, year="2026"), _value("Q3", "services", 1, year="2026")]
    later[0]["locator"] = {"total": 2, "prior_ytd": 32.0, "prior_ytd_total": 45.8}
    later[1]["locator"] = {"total": 2, "prior_ytd": 13.8, "prior_ytd_total": 45.8}
    derived = {r["kpi_key"]: (round(r["value"], 1), r["validation_status"]) for r in derive_periods(year + third + later)
               if r["fiscal_period"] == "Q4" and r["fiscal_year"] == "2025"}
    assert derived == {"products": (12.8, "verified"), "services": (5.2, "verified")}


def test_a_quarter_without_the_years_unallocated_row_counts_it_as_zero():
    from kpi_extractor.derive import derive_periods
    def row(period, key, value, end="2019-12-31", locator=None):
        return {"group_key": "segments", "group_kind": "revenue_breakdown", "kpi_key": key, "kpi_label": key, "fiscal_year": "2019",
                "fiscal_period": period, "period_end": end, "value": value, "method": "xbrl", "validation_status": "verified",
                "locator": locator or {}, "notes": []}
    values = [row("FY", "google", 160.0, locator={"total": 161.0}), row("FY", "otherbets", 0.6, locator={"total": 161.0}),
              row("FY", "unallocated", 0.4, locator={"total": 161.0})]
    for period, end in (("Q1", "2019-03-31"), ("Q2", "2019-06-30"), ("Q3", "2019-09-30")):
        values += [row(period, "google", 36.0, end, {"total": 36.15}), row(period, "otherbets", 0.15, end, {"total": 36.15})]
    q4 = {r["kpi_key"]: round(r["value"], 2) for r in derive_periods(values) if r["fiscal_period"] == "Q4"}
    assert q4 == {"google": 52.0, "otherbets": 0.15, "unallocated": 0.4}


def test_a_balancing_row_may_be_negative_in_a_derived_quarter():
    from kpi_extractor.derive import _combine_year_to_date
    year = {key: {"kpi_key": key, "value": value, "validation_status": "verified", "locator": {"total": 182527.0}, "notes": []}
            for key, value in (("googleservices", 168635.0), ("googlecloud", 13059.0), ("otherbets", 657.0), ("unallocated", 176.0))}
    nine = {"googleservices": 115762.0, "googlecloud": 9228.0, "otherbets": 461.0, "unallocated": 178.0}
    rows = _combine_year_to_date(year, nine, 125629.0, year)
    assert all(row["validation_status"] == "verified" for row in rows)
    assert {row["kpi_key"]: row["value"] for row in rows}["unallocated"] == -2.0
