"""Totals inside a metric group, learned from the group's own quarters. A list's metric group can hold lines and their
totals in order (KeyCorp's Loans by Type: four commercial lines, Total Commercial Loans, four consumer lines, Total
Consumer Loans, Total Loans) without saying which line sits within which. A total adds up to the lines listed since the
total before it, or, with none, to the totals before it. Where a group's stored quarters mostly add up so, a quarter
that does not has a line read from another table: KeyCorp's Q1 and Q2 consumer loans were replayed for eight years
from its "Summary of Nonperforming Assets" table, whose rows carry the loan types' names. No AI involved."""
from __future__ import annotations

import re

MIN_QUARTERS = 4  # a total is learned only from this many quarters that add up, more than those that do not
_TOTAL = re.compile(r"total\b", re.I)
Run = tuple[str, tuple[str, ...]]


def total_runs(lines: list[tuple[str, str]]) -> list[Run]:
    """(total, its parts) for each "Total ..." line of a group's ordered (key, label) lines."""
    runs: list[Run] = []
    since: list[str] = []
    totals: list[str] = []
    for key, label in lines:
        if not _TOTAL.match(label.strip()):
            since.append(key)
            continue
        parts = since if len(since) >= 2 else totals if len(totals) >= 2 else []
        if parts:
            runs.append((key, tuple(parts)))
        totals.append(key)
        since = []
    return runs


def _step(numbers: list[float]) -> float:
    """The coarsest power of ten every figure is a whole multiple of (their printed rounding), at most a billion."""
    step = 1e9
    while step > 1e-6 and any(abs(n / step - round(n / step)) > 1e-6 for n in numbers):
        step /= 10
    return step


def adds_up(figures: dict[str, float], run: Run) -> bool | None:
    """Whether the run's parts add up to its total, within their printed rounding; None when a figure is missing."""
    total, parts = run
    if total not in figures or any(part not in figures for part in parts):
        return None
    numbers = [figures[total], *(figures[part] for part in parts)]
    slack = max(abs(figures[total]) * 0.002, _step(numbers) * len(parts) / 2)
    return abs(sum(figures[part] for part in parts) - figures[total]) <= slack


def learned_runs(quarters: list[dict[str, float]], runs: list[Run]) -> list[Run]:
    """The runs a group's quarters (key → figure, one dict a quarter) mostly add up to."""
    kept = []
    for run in runs:
        verdicts = [found for figures in quarters if (found := adds_up(figures, run)) is not None]
        if verdicts.count(True) >= MIN_QUARTERS and verdicts.count(True) > verdicts.count(False):
            kept.append(run)
    return kept


def broken(figures: dict[str, float], runs: list[Run]) -> list[Run]:
    return [run for run in runs if adds_up(figures, run) is False]


def _quarters(rows: list[dict]) -> tuple[list[tuple[str, str]], dict[tuple[str, str], dict[str, float]]]:
    order: dict[str, tuple[int, str]] = {}
    quarters: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        order[row["kpi_key"]] = (int(row.get("kpi_order") or 0), str(row.get("kpi_label") or ""))
        quarters.setdefault((row["fiscal_year"], row["fiscal_period"]), {})[row["kpi_key"]] = float(row["value"])
    return [(key, label) for key, (_order, label) in sorted(order.items(), key=lambda item: item[1][0])], quarters


def group_runs(rows: list[dict]) -> list[Run]:
    """The totals a metric group's stored rows (any status but rejected) mostly add up to."""
    lines, quarters = _quarters(rows)
    return learned_runs(list(quarters.values()), total_runs(lines))


def unbalanced_quarters(values: dict, note: str) -> list[dict]:
    """Every served figure of a metric group's period that breaks a total the group mostly adds up to, held."""
    groups: dict[str, list[dict]] = {}
    for v in values.values():
        if v.get("group_kind") == "metric" and v["method"] in ("ai", "derived") and v["validation_status"] != "rejected" \
                and v.get("unit") == "currency":
            groups.setdefault(v["group_key"], []).append(v)
    held = []
    for rows in groups.values():
        runs = group_runs(rows)
        if not runs:
            continue
        _lines, quarters = _quarters(rows)
        for quarter, figures in quarters.items():
            if broken(figures, runs):
                held += [{**v, "validation_status": "needs_review", "notes": [*(v.get("notes") or []), note]}
                         for v in rows if (v["fiscal_year"], v["fiscal_period"]) == quarter and v["validation_status"] == "verified"]
    return held


def breaks_learned(values: dict, group_key: str, lines: list[tuple[str, str]], figures: dict[str, float]) -> bool:
    """Whether new figures for a group (key → figure) break a total its stored rows mostly add up to; the list's own
    lines give the order."""
    order = {key: (index, label) for index, (key, label) in enumerate(lines)}
    rows = [{**v, "kpi_order": order[v["kpi_key"]][0], "kpi_label": order[v["kpi_key"]][1]} for v in values.values()
            if v["group_key"] == group_key and v["kpi_key"] in order and v["method"] in ("ai", "derived")
            and v["validation_status"] != "rejected"]
    runs = group_runs(rows)
    return bool(runs) and bool(broken(figures, runs))
