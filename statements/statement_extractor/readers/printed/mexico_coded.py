"""Statements in Mexico's coded reporting template (CNBV's, as filed with BMV), which some issuers also furnish in
their 6-Ks (Televisa's quarterly "kr6kfinan" documents): every line an 8-digit account code ("10000000 TOTAL ASSETS",
"11010000 CASH AND CASH EQUIVALENTS"), columns headed "CURRENT YEAR | END OF PREVIOUS YEAR" or "CURRENT YEAR | PREVIOUS
YEAR" (cumulative and quarter), the dates only in the title ("AS OF JUNE 30, 2013 AND DECEMBER 31, 2012", "FOR THE SIX
AND THREE MONTHS ENDED JUNE 30, 2013 AND 2012"). The English labels are machine translations: "40010000 NET INCOME" is
the revenue (ingresos netos). So every line is keyed by its code, never by its label, and each statement is kept only
when its code arithmetic holds: total assets equal liabilities plus equity, the income statement's results follow one
another down to net income, and opening cash plus the change gives closing cash."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ...domain.model import FAIL, NONE, PASS, Check, Reading
from ...domain.periods import Period, label_period

MARKER = "ACCOUNT / SUBACCOUNT"
_CODE = re.compile(r"^\d{8}$")
_KINDS = (("balance", re.compile(r"statements?\s+of\s+financial\s+position|balance\s+sheets?", re.I)),
          ("cash_flow", re.compile(r"statements?\s+of\s+cash\s+flows?", re.I)),
          ("income", re.compile(r"statements?\s+of\s+(?:comprehensive\s+)?income", re.I)))
# Tables of the template that are no statement: other comprehensive income, "data information", twelve months.
_OTHER = re.compile(r"other\s+comprehensive|data\s+information|twelve\s+months", re.I)
_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
_AS_OF = re.compile(r"as\s+of\s+([a-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\s+and\s+([a-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})", re.I)
_FOR = re.compile(r"for\s+the\s+(three|six|nine|twelve)(?:\s+and\s+(three|six|nine|twelve))?\s+months\s+ended\s+([a-z]+)\s+"
                  r"(\d{1,2}),?\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})", re.I)
_SPANS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}
_SCALE = re.compile(r"\(\s*(thousands|millions)\s+of\s+([a-z\s]+?)\s*\)", re.I)
# The template's key accounts, named as the selection compares statements (selection/figures.py).
_KEY = {"10000000": "bmv_Assets", "40010000": "bmv_Revenue", "40150000": "bmv_ProfitLoss",
        "50130000": "bmv_CashAndCashEquivalents"}


def _day(month: str, day: str, year: str) -> date | None:
    number = _MONTHS.get(month.lower())
    if number is None:
        return None
    try:
        return date(int(year), number, int(day))
    except ValueError:
        return None


def _start(end: date, months: int) -> date:
    month = end.month - months + 1
    year = end.year + (month - 1) // 12
    return date(year, (month - 1) % 12 + 1, 1)


def _close(left: float, right: float, unit: float) -> bool:
    return abs(left - right) <= 2 * unit


def _sum_check(name: str, values: dict, rules: list[tuple[str, list[tuple[str, int]]]], unit: float,
               needed: int = 1) -> Check:
    held, failed = 0, []
    for total, parts in rules:
        if total not in values or not all(code in values for code, _sign in parts):
            continue
        if _close(values[total], sum(sign * values[code] for code, sign in parts), unit):
            held += 1
        else:
            failed.append(total)
    if failed:
        return Check(name, FAIL, f"accounts {', '.join(failed)} do not add up")
    return Check(name, PASS if held >= needed else NONE, f"{held} identities")


def _checks(kind: str, values: dict, unit: float) -> list[Check]:
    if kind == "balance":
        return [_sum_check("balance", values, [("10000000", [("20000000", 1), ("30000000", 1)]),
                                               ("10000000", [("11000000", 1), ("12000000", 1)])], unit, needed=2)]
    if kind == "income":  # costs printed as positive amounts, taken away
        return [_sum_check("calc", values, [
            ("40040000", [("40010000", 1), ("40020000", -1), ("40030000", -1)]),
            ("40060000", [("40040000", 1), ("40050000", 1)]),
            ("40110000", [("40060000", 1), ("40090000", 1), ("40100000", 1)]),
            ("40130000", [("40110000", 1), ("40120000", -1)]),
            ("40150000", [("40130000", 1), ("40140000", 1)]),
            ("40150000", [("40160000", 1), ("40170000", 1)])], unit, needed=3)]
    return [_sum_check("cash", values, [("50130000", [("50120000", 1), ("50100000", 1), ("50110000", 1)]),
                                        ("50100000", [("50070000", 1), ("50080000", 1), ("50090000", 1)])], unit, needed=2)]


def coded_readings(tables, *, accession: str, form: str, filed: date, document_end: date | None,
                   fiscal_year_end: str, year_offset: int) -> list[Reading]:
    readings: list[Reading] = []
    for table in tables:
        heading_rows = [row for row in table.rows[:3] if not row.figures]
        if not any(MARKER in row.label.upper() for row in heading_rows):
            continue
        titles = [(match.start(), kind) for kind, pattern in _KINDS for match in pattern.finditer(table.context)]
        if not titles:
            continue
        start, kind = max(titles)
        title = table.context[start:]
        if _OTHER.search(title):
            continue
        scale = _SCALE.search(title)
        unit = {"thousands": 1e3, "millions": 1e6}[scale.group(1).lower()] if scale else None
        if unit is None:
            continue
        currency = "MXN" if scale and "mexican" in scale.group(2).lower() else None
        coded = [row for row in table.rows if row.cells and _CODE.match(row.cells[0].text.strip()) and row.figures]
        positions = sorted({position for row in coded for position in row.figures})
        periods = _periods(kind, title, positions, " ".join(row.label for row in heading_rows))
        if not periods:
            continue
        for position, period in periods.items():
            labelled = label_period(period, fiscal_year_end, year_offset)
            if labelled is None or period.end > filed:
                continue
            values, lines = {}, []
            for row in coded:
                code = row.cells[0].text.strip()
                if position not in row.figures:
                    continue
                per_share = code in ("40180000", "40190000")
                value = row.figures[position] * (1.0 if per_share else unit)
                values[code] = value
                label = re.sub(r"^\d{8}\s*", "", row.label).strip()
                lines.append({"line": len(lines), "concept": _KEY.get(code, f"bmv_{code}"), "code": code, "label": label,
                              "level": 0 if code[2:] == "000000" else 1 if code[4:] == "0000" else 2,
                              "is_heading": False, "value": value})
            if not lines:
                continue
            readings.append(Reading(
                statement=kind, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1],
                period_end=period.end, period_start=period.start, currency=currency, accession=accession, form=form,
                filed=filed, own=document_end is not None and abs((period.end - document_end).days) <= 7, source="html",
                scope="consolidated" if "consolidated" in title.lower() else "parent", lines=lines,
                checks=_checks(kind, values, unit)))
    return readings


def _periods(kind: str, title: str, positions: list[int], heading: str) -> dict[int, Period]:
    if kind == "balance":
        match = _AS_OF.search(title)
        if not match or len(positions) != 2:
            return {}
        ends = [_day(*match.group(1, 2, 3)), _day(*match.group(4, 5, 6))]
        return {position: Period(end) for position, end in zip(positions, ends) if end}
    match = _FOR.search(title)
    if not match:
        return {}
    cumulative = _SPANS[match.group(1).lower()]
    current, previous = _day(match.group(3), match.group(4), match.group(5)), _day(match.group(3), match.group(4), match.group(6))
    if current is None or previous is None:
        return {}
    if len(positions) == 4 and "QUARTER" in heading.upper():  # current cumulative, quarter; previous cumulative, quarter
        spans = [(current, cumulative), (current, 3), (previous, cumulative), (previous, 3)]
    elif len(positions) == 2:
        spans = [(current, cumulative), (previous, cumulative)]
    else:
        return {}
    return {position: Period(end, _start(end, months)) for position, (end, months) in zip(positions, spans)}
