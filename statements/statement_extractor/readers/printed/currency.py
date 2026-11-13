"""The currency a printed statement reports in, and which of its columns are convenience translations.

A foreign filer prints its latest year a second time in another currency for readers abroad: TSMC's "2003 NT$ |
2003 US$ (Note 4)", ASML's "2004 EUR | 2004 USD". That column is the same period at an exchange rate, not a figure the
company reported, so it is left out; converting is for serving. The reporting currency is the one most columns are
printed in, else the first the title names ("In Millions of New Taiwan and U.S. Dollars"); when the statement names
none, the document's own statement of its currency or the form's decides it.
"""
from __future__ import annotations

import re
from collections import Counter

from ...domain.basis import BARE_DOLLARS, bare_currency, currencies_named
from .find import TITLE_PATTERNS
from .scales import scale_text
from .tables import Table, header_text

_DOLLAR_CELL = re.compile(r"^\s*\$\s*(?:[\d(.]|$)")


def column_currencies(table: Table, columns: list[tuple[int, ...]]) -> dict[tuple[int, ...], str | None]:
    """Each column's currency as its header names it, if it does; else as the sign printed beside its figures, where
    the title defines that sign (FEMSA's 20-Fs: "millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.)",
    the translation's column first, "$ 2,407" beside "Ps. 35,497")."""
    found = {}
    leading = " ".join(row.label for row in table.rows[:next((i for i, r in enumerate(table.rows) if r.figures), 0)][:4])
    signs = _defined_signs(table.context[-400:] + " " + " ".join(cell.text for cells in table.header for cell in cells)
                           + " " + leading)  # Coca-Cola FEMSA's FY2011 20-F titles its statements in their first rows
    for column in columns:
        named = currencies_named(header_text(table, column[0]) or header_text(table, column[-1]))
        # Two currencies over one column (TSMC's "3Q 2002 NTD" with a "USD" set off from the row below): neither.
        found[column] = named[0] if len(named) == 1 else signs.get(_sign_beside(table, column)) if signs else None
    return found


def split_by_sign(table: Table, periods: dict) -> dict:
    """A heading over two figure columns that print different signs the title defines, one column each: FEMSA's FY2011
    20-F heads "2011" over "$ 14,470" and "Ps. 201,867" (U.S. dollars, then Mexican pesos), each column its period."""
    leading = " ".join(row.label for row in table.rows[:next((i for i, r in enumerate(table.rows) if r.figures), 0)][:4])
    signs = _defined_signs(table.context[-400:] + " " + " ".join(cell.text for cells in table.header for cell in cells)
                           + " " + leading)
    if not signs:
        return periods
    split = {}
    for column, period in periods.items():
        beside = [signs.get(_sign_beside(table, (position,))) for position in column] if len(column) > 1 else []
        if beside and all(beside) and len(set(beside)) == len(beside):
            split.update({(position,): period for position in column})
        else:
            split[column] = period
    return split


_DEFINED = re.compile(r"((?:u\.?\s?s\.?|united\s+states)\s+dollars?|mexican\s+pesos?|chilean\s+pesos?|(?:brazilian\s+)?reais)"
                      r"\s*\(\s*(\$|ps\.?|ch\$|r\$|us\$)\s*\)", re.I)  # Coca-Cola FEMSA: "U.S. dollars ( $ )"


def _defined_signs(text: str) -> dict[str, str]:
    """The signs a title defines for its currencies: "U.S. dollars ($)", "Mexican pesos (Ps.)"."""
    signs = {}
    for match in _DEFINED.finditer(text):
        named = currencies_named(match.group(1))
        if named:
            signs[match.group(2).lower().rstrip(".")] = named[0]
    return signs if len(set(signs.values())) >= 2 else {}


def _sign_beside(table: Table, column: tuple[int, ...]) -> str | None:
    """The currency sign printed in the cell just before a column's figures, in the statement's first figure rows."""
    for row in [row for row in table.rows if row.figures][:12]:
        own = next((cell for cell in row.cells if cell.start == column[0]), None)
        inside = _SIGN_IN_CELL.match(own.text) if own is not None else None
        if inside:
            return inside.group(1).lower().rstrip(".")  # "Ps. 35,497"
        if own is not None and own.text.strip() and not row.figures.get(own.start) and any(
                position in row.figures for position in (column[0] + 1, column[0] + 2)):
            return own.text.strip().lower().rstrip(".")  # the sign under the heading, its figure just right (FEMSA's FY2024 20-F)
        before = [cell for cell in row.cells if cell.start < column[0] and column[0] - cell.start <= 2 and cell.text.strip()]
        if before and not row.figures.get(before[-1].start):
            return before[-1].text.strip().lower().rstrip(".")
    return None


_SIGN_IN_CELL = re.compile(r"^\(?\s*(ps\.?|us\$|ch\$|r\$|\$)\s*[\d(]", re.I)


def reporting_currency(group: list[Table], by_column: dict, form: str) -> str | None:
    text = scale_text(group)
    titles = [match.end() for pattern in TITLE_PATTERNS for match in pattern.finditer(text)]
    # The title's own words ("Expressed in Millions New Taiwan Dollars (NTD) and U.S. Dollars (USD)"), not the text
    # before it, which can name another currency.
    start = max(0, max(titles) - 120) if titles else 0
    # Past a title, the text before it is another section's: RPM's 1999 annual report discusses the euro's
    # introduction just above its balance sheet, which names no currency.
    # The table's own words first: its first rows (Petrobras's real releases print "R$ million" as the first row, after
    # commentary naming U.S. dollars just above the title), then its headings, however far before a title row (UBS's
    # restated time series: "USD million, except where indicated" over the dates, then "Income statement").
    own = " ".join(row.label for row in group[0].rows[:next((i for i, r in enumerate(group[0].rows) if r.figures), 0)][:3])
    in_title = currencies_named(own) or currencies_named(text[start:start + 400]) or currencies_named(text[start:]) or \
        currencies_named(" ".join(cell.text for cells in group[0].header for cell in cells))
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
    if in_title:
        return in_title[0]
    # Only the kind ("$ millions", "thousands of pesos"): marked, and decided by the company's own reporting currency.
    bare = bare_currency(text[start:start + 400] if titles else text)
    if bare is None and any(table.dollars or any(_DOLLAR_CELL.match(cell.text) for row in table.rows for cell in row.cells)
                            for table in group):
        # Its figures printed "$ 1,812" (Alcon's releases to 2011, "(in millions, except share data)"): the sign in a
        # cell of its own, in the figure's cell ("$ 880.2", 2004) or on a text line (2002).
        bare = BARE_DOLLARS
    return bare  # else the document's or the form's (pipeline/printed.py)


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
