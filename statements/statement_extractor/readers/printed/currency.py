"""The currency a printed statement reports in, and which of its columns are convenience translations.

A foreign filer prints its latest year a second time in another currency for readers abroad: TSMC's "2003 NT$ |
2003 US$ (Note 4)", ASML's "2004 EUR | 2004 USD". That column is the same period at an exchange rate, not a figure the
company reported, so it is left out; converting is for serving. The reporting currency is the one most columns are
printed in, else the first the title names ("In Millions of New Taiwan and U.S. Dollars"); a U.S. company's own
reports are in dollars unless they say otherwise.
"""
from __future__ import annotations

from collections import Counter

from ...domain.basis import currencies_named
from .scales import scale_text
from .tables import Table, header_text

_DOMESTIC_FORMS = ("10-K", "10-Q")  # 10-K405, 10-KT, 10-KSB, 10-QSB too


def column_currencies(table: Table, columns: list[tuple[int, ...]]) -> dict[tuple[int, ...], str | None]:
    """Each column's currency as its header names it, if it does."""
    found = {}
    for column in columns:
        named = currencies_named(header_text(table, column[0]) or header_text(table, column[-1]))
        found[column] = named[-1] if named else None
    return found


def reporting_currency(group: list[Table], by_column: dict, form: str) -> str | None:
    named = [currency for currency in by_column.values() if currency]
    if named:
        counts = Counter(named)
        best = max(counts.values())
        return next(currency for currency in named if counts[currency] == best)
    in_title = currencies_named(scale_text(group))
    if in_title:
        return in_title[0]
    return "USD" if form.upper().startswith(_DOMESTIC_FORMS) else None


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
    return dropped
