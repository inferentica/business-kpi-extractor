"""Slashed and dotted dates, and the periods a header's ranges of them give."""
from __future__ import annotations

import re
from datetime import date

from ...domain.periods import Period


def _year(year: int) -> int:
    return year + (2000 if year < 50 else 1900) if year < 100 else year


def numeric_date(match: re.Match) -> date | None:  # month first, "3/31/2005" or "3/31/05"
    month, day_of, year = (int(part) for part in match.groups())
    try:
        return date(_year(year), month, day_of)
    except ValueError:
        return None


def dotted_date(match: re.Match, day_first: bool) -> date | None:
    first, second, year = (int(part) for part in match.groups())
    day, month = (first, second) if day_first else (second, first)
    try:
        return date(_year(year), month, day)
    except ValueError:
        return None


def mended_starts(periods: dict, ranged: set) -> None:
    """A range's start set in the other order from its neighbours': Copel's June 2004 ITR heads its 2003 quarter
    "01/04/2003 a 6/30/2003" beside "01/01/2003 a 6/30/2003" (and 2004's "04/01/2004 a 6/30/2004"), which month first
    is 4 January, a second six months to the same day. A header never prints one period twice: a range that repeats
    another's length and end, and read the other way round does not, is read that way."""
    for column in sorted(ranged):
        period = periods.get(column)
        if period is None or period.start is None or period.start.day > 12 or period.start.day == period.start.month:
            continue
        others = [periods[other] for other in ranged if other != column and other in periods]
        if not any(other.end == period.end and other.duration == period.duration for other in others):
            continue
        swapped = Period(period.end, date(period.start.year, period.start.day, period.start.month))
        if swapped.start < swapped.end and swapped.duration is not None and not any(
                other.end == swapped.end and other.duration == swapped.duration for other in others):
            periods[column] = swapped

