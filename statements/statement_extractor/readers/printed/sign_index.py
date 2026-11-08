"""Every figure a printed filing shows, by row label, with the sign it is printed with: for settling a line's sign
when the filing's XBRL and its other presentations disagree. The line can sit outside the statement's face (JPM prints
its 2010 "total credit losses recognized in income" only in a note), so every table counts."""
from __future__ import annotations

from ...domain.labels import label_key
from .tables import Table


def printed_figures(tables: list[Table]) -> dict[str, list[float]]:
    figures: dict[str, list[float]] = {}
    for table in tables:
        for row in table.rows:
            if row.label and row.figures:
                figures.setdefault(label_key(row.label), []).extend(
                    figure for figure in row.figures.values() if isinstance(figure, (int, float)))
    return figures
