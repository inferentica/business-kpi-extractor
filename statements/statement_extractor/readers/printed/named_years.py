"""Years and quarters a header names, by the company's own naming of its fiscal years: a quarter named with its year
("Q3 2007", "Third Quarter 2007"), and the dates a statement's title gives each year it names."""
from __future__ import annotations

import calendar
import re
from datetime import date

from ...sources.edgar import fiscal_year_end_for

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
FULL_DATE = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}}),?\s+((?:19|20)\d{{2}})\b", re.I)

# A quarter by name and year: TSMC's "Q3 2007", "3Q 2007", "1Q08", "Third Quarter 2007", Petrobras's "3Q-2016", Copel's "2nd Q 2003", "4th Q.2003".
QUARTER_NAME = re.compile(r"\b(?:Q([1-4])|([1-4])(?:(?:st|nd|rd|th)\s*(?:Q\b|quarter)|Q)|(first|second|third|fourth)\s+quarter)(?:\s+of)?\s*[,.'\-/]?\s*((?:19|20)\d{2}|\d{2})\b", re.I)
QUARTERLY = re.compile(r"quarter|\b[1-4](?:st|nd|rd|th)?\s*Q\b|\bQ[1-4]\b", re.I)  # a quarter's column, never a year to date
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def named_quarter(text: str, fiscal_year_end: str, year_offset: int = 0) -> date | None:
    """The end of the fiscal quarter a header names, in the fiscal year it names: the company's name for it (Gildan's
    fiscal 2015 closes on January 3, 2016)."""
    match = QUARTER_NAME.search(text)
    if match is None:
        return None
    number = int(match.group(1) or match.group(2) or _ORDINAL[match.group(3).lower()])
    year = int(match.group(4))
    year += 2000 if year < 50 else 1900 if year < 100 else 0
    year -= year_offset  # the calendar year its fourth quarter ends in
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    back = month - 3 * (4 - number)
    end_year, end_month = (year, back) if back > 0 else (year - 1, back + 12)
    last = calendar.monthrange(end_year, end_month)[1]
    return date(end_year, end_month, last if day >= 28 else min(day, last))


def title_dates(text: str, fiscal_year_end: str, year_offset: int, over: set[str]) -> dict[str, tuple[str, str, str]]:
    """Each year the title dates, with its month, day and year: "June 29, 2014 and June 30, 2013" (ASML). Where the
    columns' years are the fiscal years those dates close and not the years the dates fall in, by the fiscal year:
    Kroger's "2018 | 2017 | 2016" under "Years Ended February 2, 2019, February 3, 2018 and January 28, 2017"."""
    matches = list(FULL_DATE.finditer(text))
    dated = {match.group(3): (match.group(1), match.group(2), match.group(3)) for match in matches}
    if not year_offset or not over or over <= set(dated):
        return dated
    by_name = {}
    for match in matches:
        closes = _closes(match.group(1), match.group(2), match.group(3))
        if closes is not None:
            named = fiscal_year_end_for(closes, fiscal_year_end).year + year_offset
            by_name[str(named)] = (match.group(1), match.group(2), match.group(3))
    return by_name if over <= set(by_name) else dated


def _closes(month: str, day: str, year: str) -> date | None:
    number = next((index for index, name in enumerate(calendar.month_abbr) if name and name.lower() == month[:3].lower()), None)
    try:
        return date(int(year), number, int(day)) if number else None
    except ValueError:
        return None
