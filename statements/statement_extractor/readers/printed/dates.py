"""What a printed statement's columns cover and the scale its figures are printed in: each figure column's date
and span from the header above it and the statement's title."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ...domain.periods import Period
from .find import TITLES
from .tables import Table, figure_columns, header_text


_SCALE = re.compile(r"\b(?:in|amounts in|dollars in|expressed in)\s+(?:[A-Z]{2,3}\$?\s+|US\$\s*|NT\$\s*|€\s*|euros?\s+)?"
                    r"(thousands|millions|billions)\b", re.I)


_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}


_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"


_MONTH_DAY = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}})\b", re.I)


_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


_NUMBERS = {"three": 3, "six": 6, "nine": 9, "twelve": 12, "thirteen": 13, "sixteen": 16, "seventeen": 17,
            "twenty-four": 24, "twenty-six": 26, "twenty-eight": 28, "thirty-six": 36, "thirty-nine": 39, "forty": 40,
            "forty-one": 41, "fifty-two": 52, "fifty-three": 53}


# A span in words or digits, in months or weeks: "Nine Months Ended", "12 Weeks Ended" (Costco, PepsiCo), "36 Weeks".
_SPAN = re.compile(rf"\b({'|'.join(sorted(_NUMBERS, key=len, reverse=True))}|\d{{1,2}})[-\s]+(months|weeks)\b|"
                   r"\b(?:fiscal\s+)?years?\s+ended\b|\b(?:quarter)\s+ended\b|\b(?:first|second|third|fourth)\s+quarter\b", re.I)


# A date printed in figures: PepsiCo's "3/21/09".
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")


def _span_days(count: str, unit: str) -> int:
    number = int(count) if count.isdigit() else _NUMBERS[count.lower()]
    if unit.lower() == "weeks":
        return number * 7
    return {3: 91, 6: 182, 9: 273, 12: 364}.get(number, round(number * 30.4))


def statement_scale(group: list[Table]) -> float | None:
    first = group[0]
    leading = []
    for row in first.rows:
        if row.figures:
            break
        leading.append(row.label)  # "(In millions except par value)" printed as a row above the figures
    text = first.context[-400:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in first.header) + " " + " ".join(leading)
    found = {_SCALES[match.group(1).lower()] for match in _SCALE.finditer(text)}
    return found.pop() if len(found) == 1 else None


def _date(month: str, day: str, year: str) -> date | None:
    months = {name[:3]: index for index, name in enumerate(calendar.month_name) if name}
    number = months.get(month[:3].lower().capitalize()[:3]) or next(
        (index for index, name in enumerate(calendar.month_abbr) if name and name.lower() == month[:3].lower()), None)
    try:
        return date(int(year), number, int(day)) if number else None
    except ValueError:
        return None


def column_periods(kind: str, group: list[Table], fiscal_year_end: str,
                   document_end: date | None = None) -> dict[tuple[int, ...], Period]:
    """Each figure column's period, from the header above it and the statement's title. A column printed with a year
    only ("2002 | 2001" under "Third Quarter") ends on the filing's own period date in that year when it spans less than
    a year (JPM's 2002 10-Qs), and on the fiscal year end otherwise."""
    first = group[0]
    header_all = " ".join(" ".join(cell.text for cell in cells) for cells in first.header)
    context = first.context[-300:]
    # The header's own date first, else one printed after the statement's title ("… years ended December 31, 2007"):
    # a page names other dates before the title (TSMC's auditors' report, dated April 15, 2011).
    titled = [match.end() for _, pattern in TITLES for match in pattern.finditer(context)]
    title = context[max(titled):] if titled else ""
    in_title = list(_MONTH_DAY.finditer(title))
    title_day = _MONTH_DAY.search(header_all) or (in_title[-1] if in_title else None)
    title_span = _SPAN.search(header_all) or _SPAN.search(title)
    periods: dict[tuple[int, ...], Period] = {}
    for column in figure_columns(first):
        above = header_text(first, column[0]) or header_text(first, column[-1])
        numeric = _NUMERIC_DATE.search(above)
        years = _YEAR.findall(above)
        if not years and not numeric:
            continue
        span = _SPAN.search(above) or title_span
        days = 364
        if span is not None and span.group(1):
            days = _span_days(span.group(1), span.group(2))
        elif span is not None and "quarter" in span.group(0).lower():
            days = 91
        year = years[-1] if years else None
        day = _MONTH_DAY.search(above) or title_day
        if numeric is not None and not _MONTH_DAY.search(above):
            month, day_of, year_of = (int(part) for part in numeric.groups())
            try:
                end = date(year_of + 2000 if year_of < 100 else year_of, month, day_of)
            except ValueError:
                end = None
        elif day is not None:
            end = _date(day.group(1), day.group(2), year)
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
        periods[column] = Period(end, end - timedelta(days=days - 1))
    return periods
