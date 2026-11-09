"""A company's fiscal year end as it was when a filing was made. Companies move their year end (Goldman Sachs ended
its year on the last Friday of November until 2008, then in December), so each filing takes the year end of the
annual report that closes its year; the company's current year end holds where no annual report says otherwise, or
where the two are within a couple of weeks (a 52/53-week year that ends on a different day each year)."""
from __future__ import annotations

from datetime import date

ANNUAL_FORMS = ("10-K", "10-K405", "10-KT", "10-KSB", "20-F", "40-F")
_YEAR = 375  # days from a period's end to the year end that closes it, at most
_SAME = 15  # days within which a year end is the company's usual one


def _mmdd(day: date) -> str:
    return f"{day.month:02d}{day.day:02d}"


def _gap(left: str, right: str) -> int:
    first, second = date(2001, int(left[:2]), int(left[2:])), date(2001, int(right[:2]), int(right[2:]))
    days = abs((first - second).days)
    return min(days, 365 - days)


def year_end_for(period_end: date | None, annual_ends: list[date], usual: str) -> str:
    if period_end is None:
        return usual
    closing = [end for end in annual_ends if 0 <= (end - period_end).days <= _YEAR]
    if not closing:
        return usual
    found = _mmdd(min(closing))
    return usual if _gap(found, usual) <= _SAME else found


def quarter_end_before(day: date, fiscal_year_end: str, min_gap: int = 5) -> date:
    """The latest fiscal quarter end at least `min_gap` days before `day`: the quarter a results release that day
    reports. Quarters end on the year end's day of month every three months (a 52/53-week year's dates differ by days,
    which the reader allows)."""
    import calendar
    month, day_of = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    candidates = []
    for year in (day.year - 2, day.year - 1, day.year):
        for step in range(4):
            m = (month - 1 + 3 * step) % 12 + 1
            y = year + (1 if month - 1 + 3 * step >= 12 else 0)
            last = calendar.monthrange(y, m)[1]
            candidates.append(date(y, m, min(day_of if day_of < 28 else last, last)))
    return max(end for end in candidates if (day - end).days >= min_gap)
