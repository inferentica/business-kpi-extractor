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


_STRUCTURAL = re.compile(r"\[abstract\]\s*$", re.I)


def _label(raw: str) -> str:
    """The filing's label, in sentence case where the filer wrote it in capitals (TSMC's "NET REVENUE")."""
    label = re.sub(r"\s+", " ", raw).strip()
    letters = [char for char in label if char.isalpha()]
    if len(letters) > 3 and all(char.isupper() for char in letters):
        label = label[0] + label[1:].lower()
    return label[:300]


class _ReportingCurrency:
    """A filer may tag a line twice for one period: in its own currency and as a convenience translation (TSMC's 20-F
    gives the latest year in US dollars as well). Each line takes the figure in the currency the filing reports in."""

    def __init__(self, xbrl):
        self.currency: str | None = None
        self.facts: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        try:
            facts = xbrl.facts.to_dataframe()
        except Exception:  # noqa: BLE001 - without facts the statement's own figures stand
            return
        if facts is None or facts.empty or "currency" not in facts:
            return
        if "is_dimensioned" in facts:
            facts = facts[facts["is_dimensioned"] != True]  # noqa: E712 - pandas mask
        monetary = facts[facts["currency"].notna() & ~facts["unit_ref"].astype(str).str.contains("per", case=False)]
        if monetary.empty:
            return
        self.currency = str(monetary["currency"].value_counts().idxmax())
        for row in monetary.to_dict("records"):
            when = row.get("period_end") if isinstance(row.get("period_end"), str) else row.get("period_instant")
            value = _number(row.get("numeric_value"))
            if isinstance(when, str) and value is not None:
                self.facts.setdefault((str(row["concept"]), when[:10]), []).append(
                    (str(row["currency"]), value, str(row.get("period_key"))))

    def value(self, concept: str, when: str, value: float) -> float:
        candidates = self.facts.get((concept.replace("_", ":", 1), when))
        if not candidates or self.currency is None:
            return value
        shown = next((fact for fact in candidates if fact[1] == value), None)
        if shown is None or shown[0] == self.currency:
            return value
        own = next((fact for fact in candidates if fact[0] == self.currency and fact[2] == shown[2]), None)
        return own[1] if own else value


def statement_lines(xbrl, period_end: date, annual: bool) -> tuple[list[dict], str | None]:
    """Every presented line of the three statements for the filing's own period, in the filing's order, and the
    currency the filing reports in."""
    currency = _ReportingCurrency(xbrl)
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
        # Taxonomy groupings the filer did not title ("Statement of comprehensive income [abstract]") are not lines.
        rows = [row for row in rows.to_dict("records")
                if not (row.get("abstract") and _STRUCTURAL.search(str(row.get("label") or "")))]
        levels = [int(level) for level in (_number(row.get("level")) for row in rows) if level is not None]
        base = min(levels) if levels else 0
        for duration, column in columns.items():
            when = _COLUMN.match(str(column)).group(1)
            kept = []
            for row in rows:
                heading = bool(row.get("abstract"))
                value = None if heading else _number(row.get(column))
                if not heading and value is None:
                    continue  # a line this period does not report
                if value is not None:
                    value = currency.value(str(row["concept"]), when, value)
                level = _number(row.get("level"))
                kept.append({
                    "statement": statement, "duration": duration, "line": len(kept),
                    "concept": str(row["concept"])[:200], "label": _label(str(row.get("label") or row["concept"])),
                    "level": max(0, min(20, int(level) - base)) if level is not None else 0,
                    "is_heading": heading, "value": value,
                })
            # A heading with nothing under it (a section this period does not report) is dropped.
            kept = [line for index, line in enumerate(kept) if not line["is_heading"] or _has_lines_below(kept, index)]
            for index, line in enumerate(kept):
                line["line"] = index
            lines.extend(kept)
    return lines, currency.currency


def _has_lines_below(lines: list[dict], index: int) -> bool:
    """Whether a heading has a line before the next heading at its own level or above."""
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
