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


_SPAN = re.compile(r"\b(three|thirteen|six|twenty-six|nine|thirty-nine|twelve|fifty-two|fifty-three)[-\s]+(months|weeks)\b|"
                   r"\b(?:fiscal\s+)?years?\s+ended\b|\b(?:quarter)\s+ended\b|\b(?:first|second|third|fourth)\s+quarter\b", re.I)


_SPAN_DAYS = {"three": 91, "thirteen": 91, "six": 182, "twenty-six": 182, "nine": 273, "thirty-nine": 273,
              "twelve": 364, "fifty-two": 364, "fifty-three": 371}


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
        years = _YEAR.findall(above)
        if not years:
            continue
        year = years[-1]
        day = _MONTH_DAY.search(above) or title_day
        span = _SPAN.search(above) or title_span
        days = 364
        if span is not None and span.group(1):
            days = _SPAN_DAYS.get(span.group(1).lower(), 364)
        elif span is not None and "quarter" in span.group(0).lower():
            days = 91
        if day is not None:
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
