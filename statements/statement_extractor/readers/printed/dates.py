"""What a printed statement's columns cover and the scale its figures are printed in: each figure column's date
and span from the header above it and the statement's title."""
from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import date, timedelta

from ...domain.periods import Period
from .find import TITLE_PATTERNS
from ...domain.basis import currencies_named
from .tables import YEAR_ONLY, Table, figure_columns, header_text


_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"


_MONTH_DAY = re.compile(rf"\b({_MONTH}),?\s+(\d{{1,2}})\b", re.I)  # Petrobras: "December, 31"
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH})\s+((?:19|20)\d{{2}})\d?\b", re.I)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+((?:jan(?:uary)?|feb(?:ruary)?|march|april|june|july|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                        rf"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?))\b(?!\s+\d{{1,2}}\b)", re.I)
_MONTH_YEAR_NOTE = re.compile(rf"\b({_MONTH})\s+((?:19|20)\d{{2}})\d\b", re.I)


def month_first(text: str) -> str:
    """A date as the readers take it, month first: Lloyds prints "30 Jun 2023", and a footnote mark run on to the year
    ("31 Dec 20221" for 2022, note 1)."""
    text = _DAY_MONTH_YEAR.sub(lambda m: f"{m.group(2)} {m.group(1)}, {m.group(3)}", text)
    text = _DAY_MONTH.sub(lambda m: f"{m.group(2)} {m.group(1)}", text)  # "Half-year to 30 June" over its years
    return _MONTH_YEAR_NOTE.sub(lambda m: f"{m.group(1)} {m.group(2)}", text)


_FULL_DATE = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}}),?\s+((?:19|20)\d{{2}})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH})-(\d{{2}})\b", re.I)


_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


# Delta's 2007 10-Qs after fresh start: "Two Months Ended June 30, 2007 | One Month Ended April 30, 2007"; a span read
# as none would make such a column the year to date.
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "sixteen": 16, "seventeen": 17,
            "twenty-four": 24, "twenty-six": 26, "twenty-eight": 28, "thirty-six": 36, "thirty-nine": 39, "forty": 40,
            "forty-one": 41, "fifty-two": 52, "fifty-three": 53}


# A span in words or digits, in months or weeks: "Nine Months Ended", "12 Weeks Ended" (Costco, PepsiCo), "36 Weeks".
_SPAN = re.compile(rf"\b({'|'.join(sorted(_NUMBERS, key=len, reverse=True))}|\d{{1,2}})[-\s]+(months?|weeks?)\b|"
                   r"\b(?:fiscal\s+)?years?\s+ended\b|\bhalf[-\s]year\b|\b(?:quarter)\s+ended\b|\b(?:first|second|third|fourth)\s+quarter\b", re.I)


# A date printed in figures: PepsiCo's "3/21/09".
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
_DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")  # day first: UBS's "31.12.14"


_LISTED = re.compile(r"^[\s,]*((19|20)\d{2})?[\s,]*$")  # between days listed one per column: commas, a year at most


def _standard(span) -> bool:
    """A span a statement period can have: a quarter, half, nine months or year in months, or any count of weeks."""
    return not (span.group(1) and span.group(2).lower().startswith("month")
                and _span_days(span.group(1), span.group(2)) not in (91, 182, 273, 364))


def _span_days(count: str, unit: str) -> int:
    number = int(count) if count.isdigit() else _NUMBERS[count.lower()]
    if unit.lower().startswith("week"):
        return number * 7
    return {3: 91, 6: 182, 9: 273, 12: 364}.get(number, round(number * 30.4))


def _date(month: str, day: str, year: str) -> date | None:
    months = {name[:3]: index for index, name in enumerate(calendar.month_name) if name}
    number = months.get(month[:3].lower().capitalize()[:3]) or next(
        (index for index, name in enumerate(calendar.month_abbr) if name and name.lower() == month[:3].lower()), None)
    if not number or not 1 <= int(day) <= 31:
        return None
    # "Three Months Ended February 29" over 1996 and 1995: the month's last day in the year that has no 29th.
    return date(int(year), number, min(int(day), calendar.monthrange(int(year), number)[1]))


# A quarter by name and year: TSMC's "Q3 2007", "3Q 2007", "1Q08", "Third Quarter 2007".
_QUARTER_NAME = re.compile(r"\b(?:Q([1-4])|([1-4])Q|(first|second|third|fourth)\s+quarter)(?:\s+of)?\s*[,']?\s*((?:19|20)\d{2}|\d{2})\b", re.I)
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _named_quarter(text: str, fiscal_year_end: str) -> date | None:
    """The end of the fiscal quarter a header names, in the fiscal year it names (the year its fourth quarter ends)."""
    match = _QUARTER_NAME.search(text)
    if match is None:
        return None
    number = int(match.group(1) or match.group(2) or _ORDINAL[match.group(3).lower()])
    year = int(match.group(4))
    year += 2000 if year < 50 else 1900 if year < 100 else 0
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    back = month - 3 * (4 - number)
    end_year, end_month = (year, back) if back > 0 else (year - 1, back + 12)
    last = calendar.monthrange(end_year, end_month)[1]
    return date(end_year, end_month, last if day >= 28 else min(day, last))


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


def column_periods(kind: str, group: list[Table], fiscal_year_end: str,
                   document_end: date | None = None) -> dict[tuple[int, ...], Period]:
    """Each figure column's period, from the header above it and the statement's title. A column printed with a year
    only ("2002 | 2001" under "Third Quarter") ends on the filing's own period date in that year when it spans less than
    a year (JPM's 2002 10-Qs), and on the fiscal year end otherwise."""
    first = group[0]
    header_all = month_first(" ".join(" ".join(cell.text for cell in cells) for cells in first.header))
    context = month_first(first.context[-300:])
    # The header's own date first, else one printed after the statement's title ("… years ended December 31, 2007"):
    # a page names other dates before the title (TSMC's auditors' report, dated April 15, 2011).
    titled = [match.end() for pattern in TITLE_PATTERNS for match in pattern.finditer(context)]
    title = context[max(titled):] if titled else ""
    in_title = list(_MONTH_DAY.finditer(title))
    title_day = _MONTH_DAY.search(header_all) or (in_title[-1] if in_title else None)
    periods: dict[tuple[int, ...], Period] = {}
    columns = figure_columns(first)
    in_order = years_in_order(first, columns)
    aboves = {column: month_first(header_text(first, column[0]) or header_text(first, column[-1])) for column in columns}
    # The header's span, never a span no statement period has (Morgan Stanley's "2010 | 2009 | Fiscal 2008 | One Month
    # Ended December 31, 2008": the month is its last column's alone).
    title_span = next((match for match in _SPAN.finditer(header_all) if _standard(match)), None) or _SPAN.search(title)
    header_days = list(_MONTH_DAY.finditer(header_all))
    day_first = _day_first(header_all)
    # One day for each column, in order, when no column prints a day of its own.
    named = [m.group(0).lower() for m in header_days]
    # A column's own cell may hold its neighbour's day as well, where fixed-width text runs them together (Ross's
    # "May 1, January 30, May 2" over "1999 1999 1998"); it still names its own.
    own_named = [[m.group(0).lower() for m in _MONTH_DAY.finditer(aboves[column])] for column in columns]
    in_order_days = len(header_days) == len(columns) >= 2 and len(set(named)) == len(named) and all(
        not own or own == named or named[index] in own for index, own in enumerate(own_named))
    dated_in_title = {match.group(3): (match.group(1), match.group(2)) for match in _FULL_DATE.finditer(title + " " + header_all)}
    for column in columns:
        above = aboves[column]
        numeric = _NUMERIC_DATE.search(above)
        dotted = _DOTTED_DATE.search(above) if numeric is None else None
        years = [in_order[column]] if column in in_order else _YEAR.findall(above)
        sharing = [other for other in columns if aboves[other] == above]
        if column not in in_order and len(sharing) >= 2 and len(years) == len(sharing):
            years = [years[sharing.index(column)]]  # one header cell over two columns, a year for each (TIM's 6-Ks)
        short = _MONTH_YEAR.search(above)
        if short and not years and not numeric:
            # TSMC's 1998-99 balance sheets: "Sep-99 | Jun-99", the month's last day.
            year = int(short.group(2)) + (1900 if int(short.group(2)) > 50 else 2000)
            month = _date(short.group(1), "1", str(year)).month
            end = date(year, month, calendar.monthrange(year, month)[1])
            periods[column] = Period(end) if kind == "balance" else Period(end, end - timedelta(days=90))
            continue
        named = _named_quarter(above, fiscal_year_end)
        if named is not None and kind != "balance":
            periods[column] = Period(named, named - timedelta(days=90))  # "Q3 2007", "3Q07": that quarter
            continue
        if not years and not numeric and not dotted:
            continue
        span = _SPAN.search(above) or title_span
        days = 364
        if span is not None and span.group(1):
            days = _span_days(span.group(1), span.group(2))
            if not _standard(span):
                continue  # one, two or four months is no quarter or year to date (Delta's fresh start in 2007)
        elif span is not None and span.group(0).lower().startswith("half"):
            days = 182  # Lloyds' "Half-year to 30 Jun 2023"
        elif span is not None and "quarter" in span.group(0).lower():
            days = 91
        year = years[-1] if years else None
        # The column's own date, printed lowest, over a title row's ("March 31, 2012 and December 31, 2011" over
        # "December, 31" over "2011": Petrobras).
        own_days = list(_MONTH_DAY.finditer(above))
        day = own_days[-1] if own_days else title_day
        # One cell over two columns, a day for each, as for years: "January 31, February 28," over "1996 | 1995".
        same_days = [other for other, days_named in zip(columns, own_named) if days_named == own_named[columns.index(column)]]
        listed = all(_LISTED.match(above[a.end():b.start()]) for a, b in zip(own_days, own_days[1:]))
        if len(same_days) >= 2 and len(own_days) == len(same_days) and listed:
            day = own_days[same_days.index(column)]  # never a range: KEPCO's "From Jan. 1, 2006 Through Mar. 31, 2006"
        if in_order_days:
            day = header_days[columns.index(column)]  # "AS AT JUNE 30, AND DECEMBER 31" over "2023 | 2022" (Brookfield)
        dotted_all = list(_DOTTED_DATE.finditer(above)) if dotted is not None else []
        if len(dotted_all) >= 2 and kind != "balance":
            # "04.01.2014 to 06.30.2014" (Copel's quarters): from the first date to the last.
            opened, end = _dotted(dotted_all[0], day_first), _dotted(dotted_all[-1], day_first)
            if opened is not None and end is not None and opened < end:
                periods[column] = Period(end, opened)
            continue
        if (numeric is not None or dotted is not None) and not _MONTH_DAY.search(above):
            if numeric is not None:
                month, day_of, year_of = (int(part) for part in numeric.groups())
            else:
                first, second, year_of = (int(part) for part in dotted.groups())
                day_of, month = (first, second) if day_first else (second, first)
            try:
                # Two-digit years: "3/21/09" is 2009, Oshkosh's "12/30/95" 1995 (EDGAR's filings start in 1993).
                end = date(year_of + (2000 if year_of < 50 else 1900) if year_of < 100 else year_of, month, day_of)
            except ValueError:
                end = None
        elif day is not None and not _MONTH_DAY.search(above) and year in dated_in_title:
            end = _date(*dated_in_title[year], year)  # "June 29, 2014 and June 30, 2013": each year's own date (ASML)
        elif day is not None:
            end = _date(day.group(1), day.group(2), year)
            month_named = re.search(rf"\b({_MONTH})\s+(?:19|20)\d{{2}}\b", above, re.I)
            numbered = re.search(r"\b(0?[1-9]|1[0-2])/(?:19|20)\d{2}\b", above)  # TIM's "06/2026 | 12/2025"
            month = _date(month_named.group(1), "1", year).month if month_named else int(numbered.group(1)) if numbered else None
            if month and end is not None and month != end.month:
                # "June 2023 | December 2022" under "June 30, 2023 and December 31, 2022": the column's own month, on
                # the title's day for it, else its last day.
                titled_day = next((int(match.group(2)) for match in [*_MONTH_DAY.finditer(header_all), *in_title]
                                   if _date(match.group(1), "1", year).month == month), None)
                end = date(int(year), month, titled_day or calendar.monthrange(int(year), month)[1])
        elif document_end is not None and (days < 350 and kind != "balance" or kind == "balance" and int(year) == document_end.year):
            end = date(int(year), document_end.month, min(document_end.day, calendar.monthrange(int(year), document_end.month)[1]))
        else:
            month, day_of = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
            end = date(int(year), month, min(day_of, calendar.monthrange(int(year), month)[1]))
        if end is None:
            continue
        if kind == "balance":
            periods[column] = Period(end)
            continue
        if span is None and not _near_year_end(end, fiscal_year_end):
            # No length printed and an end inside the fiscal year: the year to date, as a quarterly report presents it
            # (TIM's "2Q23 | June 2023" under "Periods ended June 30, 2023 and 2022": June 2023 is the six months).
            month, day_of = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
            year = end.year if (end.month, end.day) > (month, day_of) else end.year - 1
            opened = date(year, month, min(day_of, calendar.monthrange(year, month)[1])) + timedelta(days=1)
            periods[column] = Period(end, opened)
            continue
        periods[column] = Period(end, end - timedelta(days=days - 1))
    return periods


def _dotted(match: re.Match, day_first: bool) -> date | None:
    first, second, year = (int(part) for part in match.groups())
    day, month = (first, second) if day_first else (second, first)
    try:
        return date(year + (2000 if year < 50 else 1900) if year < 100 else year, month, day)
    except ValueError:
        return None


def _day_first(text: str) -> bool:
    """The order of a header's dotted dates: UBS prints "31.12.14", Copel "12.31.2014". A date only one order can read
    settles it; else the order that ends every date on a month's last days, where periods end; else day first."""
    dates = [(int(first), int(second)) for first, second, _year in _DOTTED_DATE.findall(text)]
    if any(first > 12 for first, _second in dates):
        return True
    if any(second > 12 for _first, second in dates):
        return False
    month_end = lambda day, month: day >= 28 or (month == 2 and day >= 27)  # noqa: E731
    if dates and all(month_end(second, first) for first, second in dates) and not all(month_end(first, second) for first, second in dates):
        return False
    return True


def _near_year_end(end: date, fiscal_year_end: str) -> bool:
    month, day_of = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    for year in (end.year - 1, end.year, end.year + 1):
        nominal = date(year, month, min(day_of, calendar.monthrange(year, month)[1]))
        if abs((nominal - end).days) <= 7:
            return True
    return False
