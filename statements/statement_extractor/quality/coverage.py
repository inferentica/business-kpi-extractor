"""What a company's read covers: the periods each filing should give, which were kept, and the run's summary."""
from __future__ import annotations

from ..selection.vintage import mixed_vintage_q4s


# AI calls this package makes, by design: none. Reported in every summary so a run's cost is on record.
AI_CALLS = 0


def expected_own(form: str, fiscal_period: str) -> list[tuple[str, str]]:
    """The (statement, duration) a filing's own period should give."""
    if form.upper().startswith(("10-K", "20-F", "40-F")):
        return [("income", "year"), ("balance", "instant"), ("cash_flow", "year")]
    flows = "quarter" if fiscal_period == "Q1" else "ytd"
    expected = [("income", "quarter"), ("balance", "instant"), ("cash_flow", flows)]
    if fiscal_period != "Q1":
        expected.append(("income", "ytd"))
    return expected


def own_period(own: list, form: str) -> dict:
    """The filing's own fiscal year and period: its balance sheet's, else any of its own columns' (a year for an annual
    report)."""
    ordered = sorted(own, key=lambda reading: reading.statement != "balance")
    if not ordered:
        return {"fiscal_year": None, "fiscal_period": None}
    annual = form.upper().startswith(("10-K", "20-F", "40-F"))
    return {"fiscal_year": ordered[0].fiscal_year, "fiscal_period": "FY" if annual else ordered[0].fiscal_period}


def summarise(symbol, profile, since, filings, readings, chosen, seconds) -> dict:
    by_status: dict[str, int] = {}
    for item in chosen:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    kept = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"])
            for item in chosen if item["status"] == "kept"}
    expected = found = 0
    missing = []
    for filing in filings:
        if not filing.get("fiscal_period"):
            continue
        for statement, duration in expected_own(filing["form"], filing["fiscal_period"]):
            expected += 1
            if (statement, filing["fiscal_year"], filing["fiscal_period"], duration) in kept:
                found += 1
            else:
                missing.append(f"{filing['form']} {filing['filed']} {statement}/{duration}")
    own_keys = {r.key for r in readings if r.own}
    return {
        "symbol": symbol, "name": profile.name, "cik": profile.cik, "fiscal_year_end": profile.fiscal_year_end,
        "foreign": profile.foreign, "since": since.isoformat(),
        "filings": len(filings), "xbrl_filings": sum(1 for f in filings if f.get("xbrl")),
        "no_xbrl_filings": [f"{f['form']} {f['filed']}" for f in filings if not f.get("xbrl")],
        "printed_filings": sum(1 for f in filings if "html" in (f.get("printed") or [])),
        "text_filings": [f"{f['form']} {f['filed']}" for f in filings if "text" in (f.get("printed") or []) and "html" not in (f.get("printed") or [])],
        "unread_filings": [f"{f['form']} {f['filed']}" for f in filings if not f.get("xbrl") and not f.get("fiscal_period")],
        "periods": len(chosen), "by_status": by_status,
        "own_expected": expected, "own_kept": found, "own_missing": missing[:40],
        "comparative_only_periods": sum(1 for item in chosen if item["status"] == "kept"
                                        and (item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]) not in own_keys),
        "restated_periods": sum(1 for item in chosen if (item.get("versus_own") or {}).get("changed")),
        "mixed_vintage_q4": mixed_vintage_q4s(chosen),
        "first_period": min((item["period_end"] for item in chosen if item["status"] == "kept"), default=None),
        "readings": len(readings), "rejected_readings": sum(1 for r in readings if r.status == "rejected"),
        "unproven_readings": sum(1 for r in readings if r.status == "unproven"),
        "ai_calls": AI_CALLS, "cost_cny": 0.0, "seconds": round(seconds),
    }
