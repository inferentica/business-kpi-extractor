"""Fiscal-period labels from a period end date and the company's fiscal year end (SEC's "MMDD")."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

# 52/53-week years end up to a week either side of the nominal fiscal year end.
_YEAR_END_SLACK = timedelta(days=10)
# Days from the previous fiscal year end to a quarter's end, for every calendar in use: 13-week and month-end quarters
# (91, 182, 273), 12/12/12/16 weeks (84, 168, 252: PepsiCo, Costco) and 16/12/12/12 weeks (112, 196, 280: Kroger),
# with room for a 52/53-week year end that falls a week from its nominal date. A date in no window is off the calendar.
_QUARTER_DAYS = {"Q1": (74, 122), "Q2": (158, 206), "Q3": (242, 290)}
# A month-end guess and the company's own quarter end differ by up to about three and a half weeks (Kroger's 16-week
# first quarter ends late May, Costco's third early May).
_OWN_END_SLACK = timedelta(days=31)


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
    at_year_end = abs((year_end - period_end).days) <= _YEAR_END_SLACK.days
    if annual:
        if not at_year_end:
            raise FiscalCalendarError(f"{period_end} is not a fiscal year end ({fiscal_year_end})")
        return fiscal_year, "FY"
    if at_year_end:
        return fiscal_year, "Q4"
    days = (period_end - _nominal_year_end(year_end.year - 1, fiscal_year_end)).days
    quarter = next((name for name, (low, high) in _QUARTER_DAYS.items() if low <= days <= high), None)
    if quarter is None:
        raise FiscalCalendarError(f"{period_end} is not a fiscal quarter end ({fiscal_year_end})")
    return fiscal_year, quarter


def nominal_quarter_end_before(filed: date, fiscal_year_end: str, *, min_gap_days: int = 5,
                               own_ends: tuple[date, ...] | list[date] = ()) -> date:
    """The latest fiscal quarter end at least min_gap_days before a filing date. Each month-end guess gives way to the
    company's own quarter end near it (own_ends: its reports' periods), or to the one a year before plus 52 weeks for a
    quarter no report has covered yet, so a week-based calendar's quarter is found: Costco's third quarter of 2025 ended
    on May 11 and was reported on May 29, before the month-end guess of May 31."""
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
            candidates.append(_own_end(date(quarter_year, month, day), own_ends))
    eligible = [candidate for candidate in candidates if candidate <= filed - timedelta(days=min_gap_days)]
    return max(eligible)


def _own_end(nominal: date, own_ends) -> date:
    near = [end for end in own_ends if abs(end - nominal) <= _OWN_END_SLACK]
    if near:
        return min(near, key=lambda end: abs(end - nominal))
    # Only a week-based calendar moves a year on by 52 weeks: a quarter that ended on its month-end date a year ago
    # (June 30) ends on it again, not on June 29.
    year_ago = date(nominal.year - 1, nominal.month, min(nominal.day, calendar.monthrange(nominal.year - 1, nominal.month)[1]))
    projected = [end + timedelta(weeks=52) for end in own_ends if abs(end - year_ago) > timedelta(days=3)
                 and abs(end + timedelta(weeks=52) - nominal) <= _OWN_END_SLACK]
    return min(projected, key=lambda end: abs(end - nominal)) if projected else nominal


def _offset_votes(samples: list[tuple[date, str, bool]], fiscal_year_end: str) -> dict[int, int]:
    votes: dict[int, int] = {}
    for period_end, reported_year, annual in samples:
        if not reported_year or not reported_year.isdigit():
            continue
        offset = int(reported_year) - fiscal_year_end_for(period_end, fiscal_year_end).year
        if offset in (-1, 0, 1):
            votes[offset] = votes.get(offset, 0) + (2 if annual else 1)  # a year's report names the year it closes
    return votes


def learn_year_offset(samples: list[tuple[date, str, bool]], fiscal_year_end: str) -> int:
    """The company's fiscal-year naming offset from (period end, reported fiscal year, annual) samples: the offset most
    of them give, an annual report's sample counting twice; on a tie, the newest sample's (samples come newest first)."""
    votes = _offset_votes(samples, fiscal_year_end)
    if not votes:
        return 0
    best = max(votes.values())
    order = [int(year) - fiscal_year_end_for(end, fiscal_year_end).year for end, year, _a in samples if year and year.isdigit()]
    return next(offset for offset in order if votes.get(offset) == best)


def offset_lead(samples: list[tuple[date, str, bool]], fiscal_year_end: str) -> int:
    """How many votes the leading offset has over the next."""
    counts = sorted(_offset_votes(samples, fiscal_year_end).values(), reverse=True) + [0, 0]
    return counts[0] - counts[1]
