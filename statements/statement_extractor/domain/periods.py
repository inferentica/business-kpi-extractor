"""Which periods a statement presents, how long each is, and the fiscal label each takes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from ..sources.edgar import FiscalCalendarError, fiscal_label


# Days a period spans, inclusive: 13-week and month-end quarters, 12- and 16-week quarters (PepsiCo, Kroger), and their
# six- and nine-month years to date, with room for a 53rd week.
_SPANS = (("quarter", 80, 122), ("ytd", 165, 210), ("ytd", 245, 295), ("year", 350, 380))


OWN_SLACK = timedelta(days=7)  # a column dated a few days off the document period still is that period


@dataclass(frozen=True)
class Period:
    end: date
    start: date | None = None  # None: an instant (a balance sheet date)

    @property
    def key(self) -> str:
        return f"instant_{self.end}" if self.start is None else f"duration_{self.start}_{self.end}"

    @property
    def days(self) -> int:
        return 0 if self.start is None else (self.end - self.start).days + 1

    @property
    def duration(self) -> str | None:
        if self.start is None:
            return "instant"
        return next((name for name, low, high in _SPANS if low <= self.days <= high), None)


def parse_period(key: str) -> Period | None:
    try:
        if key.startswith("instant_"):
            return Period(date.fromisoformat(key[8:18]))
        if key.startswith("duration_"):
            start, end = key[9:].split("_")[:2]
            return Period(date.fromisoformat(end[:10]), date.fromisoformat(start[:10]))
    except ValueError:
        return None
    return None


def presented_periods(rows: list[dict], instants: bool) -> list[Period]:
    """The periods a statement's own columns show: those most of its lines report (a period only one line carries, such
    as the opening balance a cash flow statement needs, is not a column of its own)."""
    counts: Counter[Period] = Counter()
    for row in rows:
        for key in (row.get("values") or {}):
            period = parse_period(key)
            if period is not None and (period.start is None) == instants:
                counts[period] += 1
    if not counts:
        return []
    most = max(counts.values())
    return sorted((period for period, count in counts.items() if count >= max(2, most / 2)),
                  key=lambda period: (period.end, period.days), reverse=True)


def label_period(period: Period, fiscal_year_end: str, year_offset: int) -> tuple[str, str, str] | None:
    """(fiscal year, fiscal period, duration) for a column, or None for a span no statement uses (an eight-month
    transition period) or a date off the company's calendar."""
    duration = period.duration
    if duration is None:
        return None
    try:
        if duration == "year":
            fiscal_year, fiscal_period = fiscal_label(period.end, fiscal_year_end, annual=True, year_offset=year_offset)
        else:
            fiscal_year, fiscal_period = fiscal_label(period.end, fiscal_year_end, year_offset=year_offset)
    except FiscalCalendarError:
        return None
    if duration == "instant" and fiscal_period == "Q4":
        fiscal_period = "FY"  # a year-end balance sheet is the year's, as a 10-K stores it
    if duration == "ytd" and fiscal_period not in ("Q2", "Q3"):
        return None  # six or nine months end only at a second or third quarter
    return fiscal_year, fiscal_period, duration


def opening_instant(period: Period, instants: dict[date, str]) -> str | None:
    """The balance a period opens with: the instant the day before it starts, or the nearest within a week before."""
    if period.start is None:
        return None
    day_before = period.start - timedelta(days=1)
    if day_before in instants:
        return instants[day_before]
    near = [when for when in instants if timedelta(0) <= day_before - when <= OWN_SLACK]
    return instants[max(near)] if near else None


def short_first_year(period: Period, columns: list[Period], fiscal_year_end: str, year_offset: int,
                     form: str) -> tuple[str, str, str] | None:
    """A company's first fiscal year when it began within that year: Brookfield Asset Management was formed in July 2022
    and reports July 4 to December 31, 2022 as its year 2022. Only the statement's earliest column, ending on the year
    end and at least four weeks long, and never a transition report's (10-KT) months between two year ends."""
    if form.upper().startswith("10-KT") or period.start is None or not 28 <= period.days < 350:
        return None
    if any(other.start is not None and other.start < period.start for other in columns):
        return None
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    year_end = date(period.end.year, month, min(day, 28) if month == 2 else min(day, 30 if month in (4, 6, 9, 11) else 31))
    if abs((period.end - year_end).days) > 7:
        return None
    try:
        fiscal_year, _period = fiscal_label(period.end, fiscal_year_end, annual=True, year_offset=year_offset)
    except FiscalCalendarError:
        return None
    return fiscal_year, "FY", "year"
