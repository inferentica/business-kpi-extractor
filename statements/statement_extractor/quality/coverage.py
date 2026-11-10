"""What a company's read covers: the periods each filing should give, which were kept, and the run's summary."""
from __future__ import annotations

from ..selection.vintage import mixed_vintage_q4s


# AI calls this package makes, by design: none. Reported in every summary so a run's cost is on record.
AI_CALLS = 0


def expected_own(form: str, fiscal_period: str) -> list[tuple[str, str]]:
    """The (statement, duration) a filing's own period should give."""
    if form.upper().startswith(("10-K", "20-F", "40-F", "ARS")):
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
    annual = form.upper().startswith(("10-K", "20-F", "40-F", "ARS"))
    return {"fiscal_year": ordered[0].fiscal_year, "fiscal_period": "FY" if annual else ordered[0].fiscal_period}


def _derivable(year: str, number: int, kept: set, bases: dict) -> bool:
    """A quarter's income by difference, from two periods kept on one basis: Q2 from the six months less Q1, Q3 from
    the nine months less the six, Q4 from the year less the nine (TSMC's reviewed reports print the year to date only)."""
    later = {2: ("Q2", "ytd"), 3: ("Q3", "ytd"), 4: ("FY", "year")}[number]
    earlier = {2: ("Q1", "quarter"), 3: ("Q2", "ytd"), 4: ("Q3", "ytd")}[number]
    keys = [("income", year, *later), ("income", year, *earlier)]
    # Each period counts on every basis it is kept on: TSMC's year is on IFRS in its 20-F and on Taiwan-IFRS in its
    # annual 6-K, its nine months on Taiwan-IFRS only.
    return all(key in kept for key in keys) and bool(set.intersection(*(set(bases.get(key) or ()) for key in keys)))


def _printed(filings) -> tuple[dict, set]:
    """What each quarter's releases print, by title and own column date (readers/printed/titled.py), and the quarters
    one of whose releases could not be opened: there, as in a quarter with no release on file, what it prints is
    unknown, so every statement is counted."""
    printed: dict[tuple, set] = {}
    unknown = set()
    for filing in filings:
        if filing["form"] != "6-K" or not filing.get("release_quarter"):
            continue
        quarter = tuple(filing["release_quarter"])
        if str(filing.get("note", "")).startswith("unreadable") or "titled" not in filing or any(
                ": unreadable (" in note or "too large to read" in note for note in filing.get("notes") or []):
            unknown.add(quarter)
        printed.setdefault(quarter, set()).update(map(tuple, filing.get("titled") or ()))
    return printed, unknown


def release_coverage(filings, kept: set, parent: set = frozenset(), bases: dict | None = None) -> dict:
    """A foreign filer's quarters, from the first fiscal year a release came in to its latest release quarter: each
    quarter's income, and for the first three its balance sheet and cash flows (three months or the year to date);
    the fourth quarter's balance sheet is the annual report's. A statement counts where a release printed it, was kept,
    or follows from two kept periods; quarters no release printed are listed apart (`quarters_unprinted`)."""
    quarters = sorted({tuple(f["release_quarter"]) for f in filings if f["form"] == "6-K" and f.get("release_quarter")})
    if not quarters:
        return {}
    latest = quarters[-1]
    printed, unknown = _printed(filings)
    expected = found = parent_only = derivable = 0
    missing, unprinted = [], []
    for year in range(int(quarters[0][0]), int(latest[0]) + 1):
        for number in range(1, 5):
            quarter = f"Q{number}"
            if (str(year), quarter) > latest:
                break
            wanted = [("income", [(quarter, "quarter")])]
            if number < 4:
                wanted.append(("balance", [(quarter, "instant")]))
                wanted.append(("cash_flow", [(quarter, "quarter"), (quarter, "ytd")]))
            titles = printed.get((str(year), quarter), set())
            for statement, keys in wanted:
                served = [(statement, str(year), period, duration) for period, duration in keys]
                shown = (str(year), quarter) in unknown or (str(year), quarter) not in printed or \
                    (statement,) in titles or any(key in titles for key in served)
                if any(key in kept for key in served):
                    expected += 1
                    found += 1
                    parent_only += all(key in parent or key not in kept for key in served)
                elif statement == "income" and number > 1 and _derivable(str(year), number, kept, bases or {}):
                    expected += 1
                    derivable += 1
                elif shown:
                    expected += 1
                    missing.append(f"{year} {quarter} {statement}")
                else:
                    unprinted.append(f"{year} {quarter} {statement}")
    return {"quarters_expected": expected, "quarters_kept": found, "quarters_derivable": derivable,
            "quarters_parent_only": parent_only,
            "quarters_missing": missing[:40], "quarters_unprinted": len(unprinted),
            "quarters_unprinted_list": unprinted[:120]}


def _counts(chosen, field: str, kind=lambda value: value) -> dict:
    """How many served statements carry each verdict of a witness check."""
    counts: dict[str, int] = {}
    for item in chosen:
        if item.get(field):
            counts[kind(item[field])] = counts.get(kind(item[field]), 0) + 1
    return counts


def summarise(symbol, profile, since, filings, readings, chosen, seconds) -> dict:
    by_status: dict[str, int] = {}
    for item in chosen:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    kept = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"])
            for item in chosen if item["status"] == "kept"}
    expected = found = 0
    missing = []
    for filing in filings:
        if filing["form"] == "6-K" or "asset-backed" in (filing.get("printed") or []):
            continue  # releases are counted by quarter (release_coverage); a trust's report is not the company's
        if not filing.get("fiscal_period"):
            # A filing nothing could be read from has no period of its own to name, yet its three statements are
            # missing all the same: counted as such, not left out of the coverage.
            expected += 3
            missing.append(f"{filing['form']} {filing['filed']} unread")
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
        "text_filings": sum(1 for f in filings if "text" in (f.get("printed") or [])),
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
        "witnesses": _counts(chosen, "witness"), "cash_on_balance": _counts(chosen, "cash_on_balance"),
        "quarters_to_year": _counts(chosen, "quarters", lambda value: value.split(":")[0]),
        "set_aside": sum(len(item.get("set_aside", [])) for item in chosen),
        **release_coverage(filings, kept, {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"])
                                           for item in chosen if item.get("scope") == "parent"},
                           {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]):
                            {item.get("basis"), *(other["basis"] for other in item.get("other_bases", []))}
                            for item in chosen if item["status"] == "kept"}),
        "ai_calls": AI_CALLS, "cost_cny": 0.0, "seconds": round(seconds),
    }
