"""The three financial statements as the filing presents them: every line in order, with its label and indentation.

Each 10-Q gives its quarter and year-to-date income statement, its year-to-date cash flows and its balance sheet;
each 10-K gives the year. Only the filing's own period is kept (earlier columns come from earlier filings), and only
lines without a dimension (a statement's own lines, not the breakdowns some filers tag inside it).
"""
from __future__ import annotations

import math
import re
from datetime import date

_COLUMN = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: \((Q\d|YTD|FY)\))?$")
_STATEMENTS = (("income", "income_statement"), ("balance", "balance_sheet"), ("cash_flow", "cash_flow_statement"))
_DURATIONS = {"Q1": "quarter", "Q2": "quarter", "Q3": "quarter", "Q4": "quarter", "YTD": "ytd", "FY": "year",
              None: "instant"}


def _frame(xbrl, method: str):
    statements = xbrl.statements
    reader = getattr(statements, method, None) or (
        getattr(statements, "cashflow_statement", None) if method == "cash_flow_statement" else None)
    if reader is None:
        return None
    try:
        statement = reader()
        return None if statement is None else statement.to_dataframe()
    except Exception:  # noqa: BLE001 - a filing without this statement just has none to show
        return None


def _own_columns(columns, period_end: date) -> dict[str, str]:
    """The filing's own period columns, by duration. A column a few days off the document period still counts (some
    filers end the quarter on a Saturday and date the document on the month's end)."""
    found: dict[str, tuple[int, str]] = {}
    for column in columns:
        match = _COLUMN.match(str(column))
        if not match:
            continue
        gap = abs((date.fromisoformat(match.group(1)) - period_end).days)
        if gap > 7:
            continue
        duration = _DURATIONS.get(match.group(2))
        if duration and (duration not in found or gap < found[duration][0]):
            found[duration] = (gap, column)
    return {duration: column for duration, (_gap, column) in found.items()}


def _number(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def statement_lines(xbrl, period_end: date, annual: bool) -> list[dict]:
    """Every presented line of the three statements for the filing's own period, in the filing's order."""
    lines: list[dict] = []
    for statement, method in _STATEMENTS:
        frame = _frame(xbrl, method)
        if frame is None or frame.empty or "concept" not in frame:
            continue
        columns = _own_columns(frame.columns, period_end)
        if statement != "balance":
            columns.pop("instant", None)
        if annual:
            columns = {d: c for d, c in columns.items() if d in ("year", "instant")}
        if not columns:
            continue
        rows = frame[frame["dimension"] != True] if "dimension" in frame else frame  # noqa: E712 - pandas mask
        levels = [int(level) for level in rows["level"] if _number(level) is not None] if "level" in rows else []
        base = min(levels) if levels else 0
        for duration, column in columns.items():
            kept = []
            for row in rows.to_dict("records"):
                heading = bool(row.get("abstract"))
                value = None if heading else _number(row.get(column))
                if not heading and value is None:
                    continue  # a line this period does not report
                level = _number(row.get("level"))
                kept.append({
                    "statement": statement, "duration": duration, "line": len(kept),
                    "concept": str(row["concept"])[:200], "label": str(row.get("label") or row["concept"]).strip()[:300],
                    "level": max(0, min(20, int(level) - base)) if level is not None else 0,
                    "is_heading": heading, "value": value,
                })
            # A heading with nothing under it (a section this period does not report) is dropped.
            kept = [line for index, line in enumerate(kept) if not line["is_heading"] or _has_lines_below(kept, index)]
            for index, line in enumerate(kept):
                line["line"] = index
            lines.extend(kept)
    return lines


def _has_lines_below(lines: list[dict], index: int) -> bool:
    """Whether a heading has a line before the next heading at its own level or above."""
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
