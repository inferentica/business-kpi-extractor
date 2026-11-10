"""The currency a printed statement reports in, and which of its columns are convenience translations.

A foreign filer prints its latest year a second time in another currency for readers abroad: TSMC's "2003 NT$ |
2003 US$ (Note 4)", ASML's "2004 EUR | 2004 USD". That column is the same period at an exchange rate, not a figure the
company reported, so it is left out; converting is for serving. The reporting currency is the one most columns are
printed in, else the first the title names ("In Millions of New Taiwan and U.S. Dollars"); when the statement names
none, the document's own statement of its currency or the form's decides it.
"""
from __future__ import annotations

from collections import Counter

from ...domain.basis import currencies_named
from .find import TITLE_PATTERNS
from .scales import scale_text
from .tables import Table, header_text

def column_currencies(table: Table, columns: list[tuple[int, ...]]) -> dict[tuple[int, ...], str | None]:
    """Each column's currency as its header names it, if it does."""
    found = {}
    for column in columns:
        named = currencies_named(header_text(table, column[0]) or header_text(table, column[-1]))
        # Two currencies over one column (TSMC's "3Q 2002 NTD" with a "USD" set off from the row below): neither.
        found[column] = named[0] if len(named) == 1 else None
    return found


def reporting_currency(group: list[Table], by_column: dict, form: str) -> str | None:
    text = scale_text(group)
    titles = [match.end() for pattern in TITLE_PATTERNS for match in pattern.finditer(text)]
    # The title's own words ("Expressed in Millions New Taiwan Dollars (NTD) and U.S. Dollars (USD)"), not the text
    # before it, which can name another currency.
    start = max(0, max(titles) - 120) if titles else 0
    in_title = currencies_named(text[start:start + 400]) or currencies_named(text)
    named = [currency for currency in by_column.values() if currency]
    unnamed = [currency for currency in in_title if currency not in named]
    if named and len(named) * 2 < len(by_column) and unnamed:
        # Only the translation names its currency (KEPCO's 20-F: "U.S. Dollars" over one column of four, the won
        # columns' heading lost below the years, under "In millions of Korean Won and in thousands of U.S. dollars"):
        # the columns that name none are in the title's other currency.
        return unnamed[0]
    if named:
        counts = Counter(named)
        best = max(counts.values())
        tied = [currency for currency in counts if counts[currency] == best]
        # A tie goes to the currency the title names first: its reporting currency, the other its translation.
        return next((currency for currency in in_title if currency in tied), tied[0])
    return in_title[0] if in_title else None  # else the document's or the form's (pipeline/printed.py)


def translations(columns: list[tuple[int, ...]], periods: dict, by_column: dict, reporting: str | None,
                 group: list[Table]) -> set[tuple[int, ...]]:
    """The columns that translate another column's period into a second currency."""
    dropped = {column for column in columns if by_column.get(column) and reporting and by_column[column] != reporting}
    if len(currencies_named(scale_text(group))) > 1:
        # Two currencies in the title and a period printed twice: the later column is the translation.
        seen = set()
        for column in columns:
            if column in dropped:
                continue
            key = periods[column].key
            if key in seen:
                dropped.add(column)
            seen.add(key)
    return dropped | _changes(columns, periods, group)


def _changes(columns, periods, group) -> set:
    """Columns of changes beside the amounts, their heading lost: UBS's "% change from 30.9.14 | 31.12.13" after its
    three dated columns. Such a column repeats a date printed to its left, and its figures are all percentages beside
    that column's amounts in the millions."""
    def figures(column):
        return [abs(row.figures[position]) for table in group for row in table.rows for position in column if position in row.figures]
    found, first = set(), {}
    for column in columns:
        key = periods[column].key
        if key in first:
            mine, theirs = figures(column), figures(first[key])
            if mine and theirs and max(mine) <= 1000 and max(theirs) > 100 * max(max(mine), 1):
                found.add(column)
        first.setdefault(key, column)
    return found
