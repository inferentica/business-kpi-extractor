"""Periods no document reports on its own: Q4 from the annual report, and full years from four reported quarters."""
from __future__ import annotations

QUARTERS = ("Q1", "Q2", "Q3")


def derive_periods(values: list[dict]) -> list[dict]:
    """Q4 = FY − Q1 − Q2 − Q3 where the year is reported on its own (10-Ks, annual reports); FY = Q1 + … + Q4 for breakdowns read
    from earnings releases. Only when every period lists the same KPIs, so a renamed segment never yields a bogus value."""
    by_group_year: dict[tuple[str, str], dict[str, dict[str, dict]]] = {}
    for value in values:
        if value["group_kind"] != "revenue_breakdown" or value["validation_status"] == "rejected":
            continue
        periods = by_group_year.setdefault((value["group_key"], value["fiscal_year"]), {})
        periods.setdefault(value["fiscal_period"], {})[value["kpi_key"]] = value
    derived: list[dict] = []
    for (_group, _year), periods in by_group_year.items():
        annual, quarters = periods.get("FY"), [periods.get(quarter) for quarter in QUARTERS]
        fourth = periods.get("Q4")
        if (annual and _method(annual) in ("xbrl", "ai") and all(quarters) and (fourth is None or _only_derived(fourth))
                and _same_keys(annual, *quarters)):
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
            error = (sum(row["value"] for row in rows) - total) / total
            for row in rows:
                row["reconciliation_error_pct"] = error
                row["locator"] = {"total": total}
    return rows


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
