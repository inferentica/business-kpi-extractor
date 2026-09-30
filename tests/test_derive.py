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
