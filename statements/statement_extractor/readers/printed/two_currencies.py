"""Columns that print two figures under one heading, in a statement printed in two currencies."""
from __future__ import annotations

from ...domain.basis import currencies_named


def two_columns(table, periods: dict) -> set:
    """In a statement printed in two currencies, columns under one heading that print two figures in a row: two
    columns, the heading of one lost. FEMSA's FY2009
    20-F spans "2008" over its 2009 peso column and its 2008 one, beside "2009" over the dollar translation; neither
    the year nor which figure is the period's can be told, so neither is read."""
    if len(set(currencies_named(table.context[-400:]))) < 2:
        return set()  # only a statement printed in two currencies, its translation beside it
    return {column for column in periods if len(column) > 1 and any(
        sum(1 for position in column if position in row.figures) > 1 for row in table.rows)}
