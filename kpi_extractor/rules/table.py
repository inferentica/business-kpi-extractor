"""Table layout the rules share: the heading over a column, and the nearest heading or section heading over a row."""

from __future__ import annotations

from ..documents.document import parse_number, Table


def _heading(row: list[str], col: int) -> str:
    # A header spanning several columns sits on the first of them ("Quarter Ended December 31," over 2022 and 2023):
    # an empty header cell takes the nearest one to its left, never the row-label column.
    for index in range(min(col, len(row) - 1), 0, -1):
        if row[index].strip():
            return row[index]
    return ""


def _nearest_heading(table: Table, row: int) -> str:
    """The heading row just above a row (a label with no figures), or "" when the row above has figures."""
    index = row - 1
    cells = table.rows[index] if 0 <= index < len(table.rows) else []
    return " ".join(cells) if cells and not any(parse_number(cell) for cell in cells[1:]) else ""


def _section_heading(table: Table, row: int) -> str:
    """The heading rows just above a row inside its table (a label with no figures): PepsiCo's "Impact on % Change of:"
    over its organic volume change, whose cells print no % sign."""
    headings = []
    for index in range(row - 1, max(-1, row - 4), -1):
        cells = table.rows[index] if index < len(table.rows) else []
        if not cells or any(parse_number(cell) for cell in cells[1:]):
            break
        headings.append(" ".join(cells))
    return " ".join(headings)
