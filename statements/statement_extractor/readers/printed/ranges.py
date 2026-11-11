"""Slashed and dotted dates, and the periods a header's ranges of them give."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from ...domain.periods import Period
from ...domain.basis import currencies_named
from .tables import YEAR_ONLY, Table, header_text


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


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def own_month(above: str) -> int | None:
    """The month a column's own header names before its year ("December 2011"), if it names one."""
    match = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}\b", above, re.I)
    return _MONTHS.index(match.group(1).lower()) + 1 if match else None



_FULL_NUMERIC = re.compile(r"\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})")


def dates_in_order(table: Table, columns: list[tuple[int, ...]]) -> dict[tuple[int, ...], str]:
    """Each column's date taken in order from a header row of dates alone, one for each column, where the row's cells
    sit left of the figures: Ultrapar's 2010 6-Ks print "06/30/2010 | 06/30/2009 | 06/30/2010 | 06/30/2009" over its
    parent's and consolidated columns a cell to the left, so each column read its left neighbour's date (round 61)."""
    for cells in reversed(table.header):
        texts = [cell.text.strip() for cell in cells if cell.text.strip()]
        if len(texts) == len(columns) >= 2 and all(_FULL_NUMERIC.fullmatch(text) for text in texts):
            above = [_FULL_NUMERIC.findall(header_text(table, column[0]) or header_text(table, column[-1])) for column in columns]
            if any(texts[index] not in found for index, found in enumerate(above)):
                return dict(zip(columns, texts))
            return {}
    return {}


def years_in_order(table: Table, columns: list[tuple[int, ...]]) -> dict[tuple[int, ...], str]:
    """Each column's year taken in order from a row of bare years, when that row prints one year for each column in
    the reporting currency: TSMC's FY2002 20-F prints "2000 | 2001 | 2002" a cell left of its figures, so "2001" sits
    right over 2000's. A translation column takes the year of the column before it."""
    row = next((cells for cells in reversed(table.header)
                if len(years := [cell for cell in cells if YEAR_ONLY.match(cell.text)]) >= 2
                and all(YEAR_ONLY.match(cell.text) or re.fullmatch(r"notes?", cell.text, re.I) for cell in cells if cell.text)), None)
    if row is None:
        return {}
    years = [cell.text for cell in row if YEAR_ONLY.match(cell.text)]
    named = {column: currencies_named(header_text(table, column[0])) for column in columns}
    counts = Counter(codes[-1] for codes in named.values() if codes)
    reporting = counts.most_common(1)[0][0] if counts else None
    own = [column for column in columns if not (named[column] and named[column][-1] != reporting)]
    if len(own) != len(years) or len(own) == len(columns) and len(row) == len(columns):
        return {}
    found, year = {}, None
    for column in columns:
        if column in own:
            year = years[own.index(column)]
        if year is not None:
            found[column] = year
    return found
