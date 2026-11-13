"""A statement that prints the parent company's columns beside the group's: Brazil's quarterly information (TIM's 6-Ks:
"Parent Company | Consolidated" over "June 2023 | December 2022 | December 2022"). The parent's columns come first; the
group's start where the dates start again. Each column keeps the scope of the heading over it, so a parent figure is
never served as the group's."""
from __future__ import annotations

import re

from .tables import Table

# The two headings side by side, the parent's first: as the table's own row ("Parent Company Consolidated"), or in
# the header above the columns.
# Not the report's name ("the individual and consolidated quarterly information"). Ultrapar's 2010 6-Ks print "Parent |
# Consolidated"; Copel's 2003 quarterly information "Company | Consolidated", capitalised as headings.
SIDE_BY_SIDE = re.compile(r"\b(parent(?:\s+company)?|individual)\s+consolidated\b|(?-i:\bCompany\s+Consolidated\b)", re.I)


def _side_by_side(group: list[Table]) -> bool:
    first = group[0]
    leading = " ".join(row.label for row in first.rows[:6] if not row.figures)
    header = " ".join(" ".join(cell.text for cell in cells) for cells in first.header)
    return bool(SIDE_BY_SIDE.search(leading + " " + header))


def column_scopes(group: list[Table], dated: list, periods: dict) -> dict | None:
    """Each dated column's scope: "parent" up to where the dates start again, "consolidated" from there; empty for a
    statement of one scope. None when the columns do not fall into two such runs (whose column is whose is unknown)."""
    if not _side_by_side(group):
        return {}
    ends = [periods[column].end for column in dated]
    restart = next((index for index in range(1, len(ends)) if ends[index] >= ends[index - 1]), None)
    if restart is None or any(ends[index] >= ends[index - 1] for index in range(restart + 1, len(ends))):
        return None
    return {column: "parent" if index < restart else "consolidated" for index, column in enumerate(dated)}
