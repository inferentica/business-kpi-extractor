"""Slashed and dotted dates, and the periods a header's ranges of them give."""
from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import date, timedelta

from ...domain.periods import Period
from ...domain.basis import currencies_named
from .tables import YEAR_ONLY, Table, header_text


def _year(year: int) -> int:
    return year + (2000 if year < 50 else 1900) if year < 100 else year


def numeric_date(match: re.Match, day_first: bool = False) -> date | None:  # "3/31/2005", "3/31/05"; Copel's "30/06/2009"
    month, day_of, year = (int(match.group(index)) for index in ((2, 1, 3) if day_first else (1, 2, 3)))
    try:
        return date(_year(year), month, day_of)
    except ValueError:
        return None


def slashed_day_first(text: str) -> bool:
    """Slashed dates read day first only where one can only be read so and none only month first: Copel's 2009 ITRs
    print "30/06/2009 | 31/03/2009", its 2004 ITR "01/04/2003 a 6/30/2003", PepsiCo "3/21/09"."""
    dates = [(int(first), int(second)) for first, second in re.findall(r"\b(\d{1,2})/(\d{1,2})/(?:\d{4}|\d{2})\b", text)]
    return any(first > 12 for first, _second in dates) and not any(second > 12 for _first, second in dates)


def off_title(kind: str, end: date, dated_in_title: dict) -> bool:
    """A flow's figure-dated column whose end is not the title's for its year: Copel's Q1 2010 ITR prints "31/12/2009"
    over the first quarter of 2009 under "As of March 31, 2010 and March 31, 2009". Left out, never dated either way."""
    named = dated_in_title.get(str(end.year))
    if kind == "balance" or named is None:
        return False
    stated = _named_day(*named, end.year)
    return stated is not None and stated != end


def _named_day(month: str, day: str, year: int) -> date | None:
    number = next((index for index, name in enumerate(calendar.month_abbr) if name and name.lower() == month[:3].lower()), None)
    try:
        return date(year, number, int(day)) if number else None
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



_FULL_NUMERIC = re.compile(r"\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b")


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


def spans_over(table: Table, columns: list[tuple[int, ...]], span) -> dict[tuple[int, ...], str]:
    """Each column's span printed in the rows over the figures, a cell over each column: Lloyds' quarterly releases
    print "Nine | Nine | Three | Three" over "months | months | months | months" over "ended 30 Sept" (round 62)."""
    found: dict[tuple[int, ...], list[str]] = {column: [] for column in columns}
    for row in table.rows[:next((index for index, row in enumerate(table.rows) if row.figures), 0)][:6]:
        over = {column: cell.text for column in columns for cell in row.cells[1:]
                if cell.text.strip() and cell.start <= column[0] < cell.start + cell.span}
        if len(over) >= 2:
            for column, text in over.items():
                found[column].append(text)
    return {column: " ".join(texts) for column, texts in found.items() if texts and span.search(" ".join(texts))}


# "Quarter and Year to date on June 30, 2014 and 2013" (Embraer's 6-Ks), "For the three-month and nine-month periods
# ended September 30, 2015 and 2014" (Shinhan's): the title's order of the two spans each date is printed with.
_QUARTER_FIRST = re.compile(r"\b(?:quarter|three[\s-]+months?)\s+and\s+(?:the\s+)?(?:year[\s-]+to[\s-]+date|six|nine)\b", re.I)
_TO_DATE_FIRST = re.compile(r"\b(?:year[\s-]+to[\s-]+date|six|nine)(?:[\s-]+months?)?\s+and\s+(?:the\s+)?(?:quarter|three)\b", re.I)


def _signs_beside(table: Table, position: int, after: int = 0) -> set[str]:
    """The signs printed just before a figure position, after `after`, in the first figure rows ("$", "Ps.", "W"):
    short cells with no digit, never a row's label."""
    found = set()
    for row in [row for row in table.rows if row.figures][:6]:
        found |= {cell.text.strip() for cell in row.cells
                  if after < cell.start < position and position - cell.start <= 2 and cell.start not in row.figures
                  and 0 < len(cell.text.strip()) <= 4 and not re.search(r"\d", cell.text)
                  and re.search(r"[^\W\d_]|[$€£¥₩]", cell.text)}  # never a negative figure's closing ")" (KEPCO)
    return found


def paired_spans(periods: dict, columns: list, text: str, fiscal_year_end: str, table: Table) -> None:
    """The quarter and the year to date of one date, under a title naming both, in the title's order: two columns side
    by side read as the same span ("06.30.2014 | 06.30.2014", Embraer), or one year's heading over two figure columns
    (Shinhan's "2015" over its three and nine months). In the first quarter the two are one span: nothing changes."""
    quarter_first, to_date_first = _QUARTER_FIRST.search(text), _TO_DATE_FIRST.search(text)
    if bool(quarter_first) == bool(to_date_first):
        return  # neither, or both: the order is not the title's to give
    def spans(end: date) -> tuple:
        month = end.month - 2
        quarter = Period(end, date(end.year + (month - 1) // 12, (month - 1) % 12 + 1, 1))
        fiscal_month, fiscal_day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
        year = end.year if (end.month, end.day) > (fiscal_month, fiscal_day) else end.year - 1
        to_date = Period(end, date(year, fiscal_month, min(fiscal_day, calendar.monthrange(year, fiscal_month)[1])) + timedelta(days=1))
        return (quarter, to_date) if quarter_first else (to_date, quarter)
    dated = [column for column in columns if column in periods]
    for column in dated:  # one heading over two figure columns
        period = periods[column]
        # Never a translation beside its currency ("$ 14,470 | Ps. 201,867": FEMSA's, currency.split_by_sign); a sign
        # before the first column alone is the statement's own (Shinhan's "W 2,744,317 | 8,374,623").
        signs = (_signs_beside(table, column[0]), _signs_beside(table, column[1], column[0])) if len(column) == 2 else (set(), set())
        if len(column) == 2 and period.start is not None and spans(period.end)[0] != spans(period.end)[1] \
                and not (all(signs) and signs[0] != signs[1]):
            del periods[column]
            periods.update(dict(zip(((column[0],), (column[1],)), spans(period.end))))
    for left, right in zip(dated, dated[1:]):  # two columns read as the same span
        if left in periods and right in periods and periods[left] == periods[right] and periods[left].start is not None:
            first, second = spans(periods[left].end)
            if first != second:
                periods[left], periods[right] = first, second


def counted_start(end: date, days: int, weekly: bool = False) -> date:
    """A span's first day: whole months back from a month's last day ("Six months ended June 30" opens on 1 January,
    not 31 December as 182 days back did), else its days back (52- and 53-week years, weeks)."""
    if weekly or end.day != calendar.monthrange(end.year, end.month)[1]:
        return end - timedelta(days=days - 1)
    month = end.month - max(1, round(days / 30.4)) + 1
    return date(end.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
