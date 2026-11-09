"""The EX-27 financial data schedule: an independent cross-check for filings of 1993-2001.

Every 10-K and 10-Q of those years carries a schedule that tags the period's key figures (<TOTAL-ASSETS>,
<NET-INCOME>, scaled by <MULTIPLIER>), and some carry restated schedules for earlier periods. A statement read from
the printed tables for the same period must show the schedule's total assets (a balance sheet) or net income (an
income statement) among its lines, within the rounding of the coarser of the two. Both tags exist in every article
of the schedule (commercial companies, banks, insurers), unlike revenue, which banks do not tag.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..domain.model import FAIL, PASS, Check, Reading

_TAG = re.compile(r"<([A-Z0-9-]+)>\s*([^<\n]*)")
_MONTHS = {name: index for index, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
# The schedule's own period types, as statement durations.
_DURATIONS = {"YEAR": "year", "12-MOS": "year", "3-MOS": "quarter", "6-MOS": "ytd", "9-MOS": "ytd"}


@dataclass
class Schedule:
    period_end: date
    duration: str | None  # year | quarter | ytd
    multiplier: float
    total_assets: float | None
    net_income: float | None
    stated: bool = True  # whether the schedule states its <MULTIPLIER>


def _date(raw: str) -> date | None:
    match = re.match(r"([A-Z]{3})-(\d{1,2})-(\d{4})", raw.strip().upper())
    if not match or match.group(1) not in _MONTHS:
        return None
    try:
        return date(int(match.group(3)), _MONTHS[match.group(1)], int(match.group(2)))
    except ValueError:
        return None


def _amount(raw: str) -> float | None:
    text = raw.strip().replace(",", "")
    negative = text.startswith("(") or text.startswith("-")
    text = text.strip("()-")
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_schedule(text: str) -> Schedule | None:
    tags: dict[str, str] = {}
    for name, value in _TAG.findall(text):
        tags.setdefault(name, value)
    end = _date(tags.get("PERIOD-END", ""))
    if end is None:
        return None
    multiplier = _amount(tags.get("MULTIPLIER", "1")) or 1.0
    assets, income = _amount(tags.get("TOTAL-ASSETS", "")), _amount(tags.get("NET-INCOME", ""))
    return Schedule(end, _DURATIONS.get(tags.get("PERIOD-TYPE", "").strip().upper()), multiplier,
                    None if assets is None else assets * multiplier, None if income is None else income * multiplier,
                    "MULTIPLIER" in tags)


def _unit(reading: Reading) -> float:
    """The coarsest power of ten every amount in the statement is a multiple of (its printed scale), up to a million."""
    values = [abs(line["value"]) for line in reading.lines if line["value"] and abs(line["value"]) >= 1000]
    unit = 1e6
    while unit > 1 and any(round(value / unit, 6) % 1 for value in values):
        unit /= 10
    return unit


def schedule_check(reading: Reading, schedules: list[Schedule]) -> Check | None:
    """The schedule for the reading's period, compared with the statement's lines; None when no schedule covers it."""
    if reading.statement not in ("balance", "income"):
        return None
    for schedule in schedules:
        if abs((schedule.period_end - reading.period_end).days) > 7:
            continue
        if reading.statement == "income" and schedule.duration not in (None, reading.duration):
            continue
        figure = schedule.total_assets if reading.statement == "balance" else schedule.net_income
        if figure is None:
            continue
        # A schedule that states no multiplier (EOG's of 2000, which EDGAR flagged as processed with errors) is in the
        # statement's own scale, whatever that is: its figure in units, thousands or millions.
        for multiplier in ((schedule.multiplier,) if schedule.stated else (1.0, 1e3, 1e6)):
            scaled = figure * multiplier / schedule.multiplier
            tolerance = 0.5 * max(multiplier, _unit(reading)) + 1e-6
            if any(line["value"] is not None and abs(line["value"] - scaled) <= tolerance for line in reading.lines):
                return Check("ex27", PASS, "" if schedule.stated else f"schedule states no multiplier; read as x{multiplier:,.0f}")
        name = "total assets" if reading.statement == "balance" else "net income"
        return Check("ex27", FAIL, f"EX-27 {name} {figure:,.0f} is not among the statement's lines")
    return None
