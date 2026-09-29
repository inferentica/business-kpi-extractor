"""Fiscal-period labels from a period end date and the company's fiscal year end (SEC's "MMDD")."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

# 52/53-week years end up to a week either side of the nominal fiscal year end.
_YEAR_END_SLACK = timedelta(days=10)
_QUARTER_BY_MONTHS_BEFORE_YEAR_END = {0: "Q4", 3: "Q3", 6: "Q2", 9: "Q1"}


class FiscalCalendarError(ValueError):
    pass


def _nominal_year_end(year: int, fiscal_year_end: str) -> date:
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def fiscal_year_end_for(period_end: date, fiscal_year_end: str) -> date:
    """The nominal end of the fiscal year that contains period_end."""
    end = _nominal_year_end(period_end.year, fiscal_year_end)
    if end < period_end - _YEAR_END_SLACK:
        end = _nominal_year_end(period_end.year + 1, fiscal_year_end)
    elif end - period_end > timedelta(days=366) - _YEAR_END_SLACK:
        end = _nominal_year_end(period_end.year - 1, fiscal_year_end)
    return end


def fiscal_label(period_end: date, fiscal_year_end: str, *, annual: bool = False, year_offset: int = 0) -> tuple[str, str]:
    """(fiscal year, "Q1".."Q4" or "FY").

    Companies name their fiscal year differently (NVIDIA's FY2027 ends January 2027; Home Depot's fiscal 2025 ends
    February 2026), so year_offset carries the company's own convention, learned from its XBRL filings.
    """
    year_end = fiscal_year_end_for(period_end, fiscal_year_end)
    fiscal_year = str(year_end.year + year_offset)
    months_before = round((year_end - period_end).days / 30.44)
    if annual:
        if months_before != 0:
            raise FiscalCalendarError(f"{period_end} is not a fiscal year end ({fiscal_year_end})")
        return fiscal_year, "FY"
    quarter = _QUARTER_BY_MONTHS_BEFORE_YEAR_END.get(months_before)
    if quarter is None:
        raise FiscalCalendarError(f"{period_end} is not a fiscal quarter end ({fiscal_year_end})")
    return fiscal_year, quarter


def nominal_quarter_end_before(filed: date, fiscal_year_end: str, *, min_gap_days: int = 5) -> date:
    """The latest nominal fiscal quarter end at least min_gap_days before a filing date."""
    candidates = []
    for year in (filed.year - 1, filed.year, filed.year + 1):
        year_end = _nominal_year_end(year, fiscal_year_end)
        for months_back in (0, 3, 6, 9):
            month = year_end.month - months_back
            quarter_year = year_end.year
            while month <= 0:
                month += 12
                quarter_year -= 1
            day = min(year_end.day, calendar.monthrange(quarter_year, month)[1])
            # Month-end fiscal calendars keep quarters on month ends (e.g. Jun 30 for a Dec 31 year end).
            if year_end.day == calendar.monthrange(year_end.year, year_end.month)[1]:
                day = calendar.monthrange(quarter_year, month)[1]
            candidates.append(date(quarter_year, month, day))
    eligible = [candidate for candidate in candidates if candidate <= filed - timedelta(days=min_gap_days)]
    return max(eligible)


def learn_year_offset(samples: list[tuple[date, str, bool]], fiscal_year_end: str) -> int:
    """The company's fiscal-year naming offset from (period end, reported fiscal year, annual) samples."""
    votes: dict[int, int] = {}
    for period_end, reported_year, _annual in samples:
        if not reported_year or not reported_year.isdigit():
            continue
        offset = int(reported_year) - fiscal_year_end_for(period_end, fiscal_year_end).year
        if offset in (-1, 0, 1):
            votes[offset] = votes.get(offset, 0) + 1
    return max(votes, key=votes.get) if votes else 0
