"""Periods no document reports on its own: Q4 from the annual report, and full years from four reported quarters."""
from __future__ import annotations

QUARTERS = ("Q1", "Q2", "Q3")


def derive_periods(values: list[dict]) -> list[dict]:
    """Q4 = FY − Q1 − Q2 − Q3 where the year is reported on its own (10-Ks, annual reports); FY = Q1 + … + Q4 for breakdowns read
    from earnings releases. Only when every period lists the same KPIs, so a renamed segment never yields a bogus value."""
    by_group_year: dict[tuple[str, str], dict[str, dict[str, dict]]] = {}
    for value in values:
        # Only verified figures feed the arithmetic: a flagged partial reading (TSMC's release naming a few nodes beside
        # the report's full table) must neither block a derived quarter nor enter one.
        if value["group_kind"] != "revenue_breakdown" or value["validation_status"] != "verified":
            continue
        periods = by_group_year.setdefault((value["group_key"], value["fiscal_year"]), {})
        periods.setdefault(value["fiscal_period"], {})[value["kpi_key"]] = value
    derived: list[dict] = []
    for (group, year), periods in by_group_year.items():
        annual, quarters = periods.get("FY"), [periods.get(quarter) for quarter in QUARTERS]
        fourth = periods.get("Q4")
        if annual and all(quarters):
            quarters = [_align(annual, quarter) for quarter in quarters]
        if annual and _method(annual) in ("xbrl", "ai") and (fourth is None or _only_derived(fourth)):
            nine = _nine_months(annual, periods.get("Q3"), by_group_year.get((group, str(int(year) + 1)), {}).get("Q3"))
            if nine:
                # Q4 = year − nine months, the nine months on the year's own basis: the year's Q3 10-Q, or when the
                # 10-K recast the breakdown (Microsoft split out Dynamics), next year's Q3 10-Q restating them.
                derived.extend(_combine_year_to_date(annual, *nine))
            elif all(quarters) and _same_keys(annual, *quarters):
                derived.extend(_combine(annual, quarters, "Q4"))
        if not annual or _only_derived(annual):
            parts = [*quarters, fourth]
            if all(parts) and _same_keys(*parts) and all(_method(part) == "ai" for part in parts):
                derived.extend(_combine(None, parts, "FY"))
    return derived


def _combine(annual: dict | None, quarters: list[dict], target: str) -> list[dict]:
    rows = []
    for key in (annual or quarters[-1]):
        parts = [quarter[key] for quarter in quarters]
        base = annual[key] if annual else parts[-1]
        if annual:
            value = annual[key]["value"] - sum(part["value"] for part in parts)
        else:
            value = sum(part["value"] for part in parts)
        sources = [*parts, *([annual[key]] if annual else [])]
        verified = all(source["validation_status"] == "verified" for source in sources)
        notes = [] if verified else ["derived from values that need review"]
        if annual and value < 0 <= annual[key]["value"]:
            verified, notes = False, [*notes, "derived Q4 is negative"]
        rows.append({
            **base,
            "fiscal_period": target,
            "period_end": base["period_end"],
            "value": value,
            "method": "derived",
            "validation_status": "verified" if verified else "needs_review",
            "reconciliation_error_pct": None,
            "notes": notes,
            "locator": None,
        })
    # Carry the reported revenue total through the same arithmetic, so derived periods still reconcile.
    totals = [_total(period) for period in quarters]
    annual_total = _total(annual) if annual else None
    if all(total is not None for total in totals) and (annual is None or annual_total is not None):
        total = annual_total - sum(totals) if annual else sum(totals)
        if total:
            _reconcile(rows, total)
    return rows


def _align(annual: dict, quarter: dict) -> dict:
    """A quarter laid out like its year before subtracting:
    - rows the year folds into "Other" (Nvidia's 10-K shows four regions, its 10-Qs six) are added to the quarter's
      "Other";
    - an immaterial row the year does not list (TSMC's 10nm, under 1% of the quarter) is left out."""
    extra = [key for key in quarter if key not in annual]
    if not extra:
        return quarter
    aligned = {key: value for key, value in quarter.items() if key in annual}
    total = sum(float(value["value"]) for value in quarter.values()) or 1.0
    folded = 0.0
    for key in extra:
        value = float(quarter[key]["value"])
        if "other" in annual and "other" in quarter:
            folded += value
        elif abs(value) >= 0.01 * abs(total):
            return quarter  # a material row the year lacks: the two do not describe one breakdown
    if folded:
        aligned["other"] = {**quarter["other"], "value": float(quarter["other"]["value"]) + folded}
    return aligned


def _nine_months(annual: dict, third: dict | None, next_third: dict | None) -> tuple[dict, float, dict] | None:
    """(member → nine-month value, their total, the rows they came from) on the year's basis, if any report has them."""
    candidates = []
    if third:
        candidates.append((third, "ytd", "ytd_total"))
    if next_third:
        candidates.append((next_third, "prior_ytd", "prior_ytd_total"))
    for rows, field, total_field in candidates:
        values = {key: (row.get("locator") or {}).get(field) for key, row in rows.items()}
        values = {key: value for key, value in values.items() if isinstance(value, (int, float))}
        totals = [(row.get("locator") or {}).get(total_field) for row in rows.values()]
        total = next((t for t in totals if isinstance(t, (int, float))), None)
        if total is not None and set(values) == set(annual):
            return values, float(total), rows
    return None


def _combine_year_to_date(annual: dict, nine: dict, nine_total: float, sources: dict) -> list[dict]:
    rows = []
    for key, year in annual.items():
        value = year["value"] - nine[key]
        verified = year["validation_status"] == "verified" and sources[key]["validation_status"] == "verified"
        notes = [] if verified else ["derived from values that need review"]
        if value < 0 <= year["value"]:
            verified, notes = False, [*notes, "derived Q4 is negative"]
        rows.append({**year, "fiscal_period": "Q4", "value": value, "method": "derived",
                     "validation_status": "verified" if verified else "needs_review",
                     "reconciliation_error_pct": None, "notes": notes, "locator": None})
    annual_total = _total(annual)
    if annual_total is not None:
        total = annual_total - nine_total
        if total:
            _reconcile(rows, total)
    return rows


MAX_DERIVED_GAP = 0.001


def _reconcile(rows: list[dict], total: float) -> None:
    """A derived period is verified only if its parts add up to its derived total, like any reported one."""
    error = (sum(row["value"] for row in rows) - total) / total
    for row in rows:
        row["reconciliation_error_pct"] = error
        row["locator"] = {"total": total}
        if abs(error) > MAX_DERIVED_GAP and row["validation_status"] == "verified":
            row["validation_status"] = "needs_review"
            row["notes"] = [*row["notes"], f"derived parts differ from the derived total by {error:.1%}"]


def _total(period: dict) -> float | None:
    locator = next(iter(period.values())).get("locator") or {}
    total = locator.get("total")
    return float(total) if isinstance(total, (int, float)) else None


def _same_keys(*periods: dict) -> bool:
    keys = set(periods[0])
    return all(set(period) == keys for period in periods[1:])


def _method(period: dict) -> str:
    return next(iter(period.values()))["method"]


def _only_derived(period: dict | None) -> bool:
    return bool(period) and all(value["method"] == "derived" for value in period.values())
