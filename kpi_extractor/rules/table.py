"""Table layout the rules share: the heading over a column, and the nearest heading or section heading over a row."""

from __future__ import annotations

import re

from ..documents.document import clean, Document, parse_number, Table


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


def _shape_words(cell: str) -> str:
    # A footnote mark run into a heading ("Unit Case Volume3") does not change it.
    return " ".join(re.findall(r"[a-z]+", re.sub(r"(?<=[a-z]{3})\d{1,2}\b", "", clean(cell or "").lower())))


def _shape(table: Table) -> tuple | None:
    """A table's columns and rows by their names alone: its first row's headings and every row's label."""
    if len(table.rows) < 3 or len(table.rows[0]) < 3:
        return None
    header = tuple(_shape_words(cell) for cell in table.rows[0])
    labels = tuple(_shape_words(row[0]) if row else "" for row in table.rows[1:])
    if sum(1 for cell in header if cell) < 2 or sum(1 for label in labels if label) < 2:
        return None
    return header, labels


def earlier_twin(document: Document, table_id: str) -> str | None:
    """The earlier table a table repeats in shape, with other figures, when neither names its period: Coca-Cola's Q4
    release gives the quarter's "Revenues and Volume" table, then the full year's with the same columns and rows and no
    heading or date telling them apart. A release gives the quarter's first."""
    table = document.tables.get(table_id)
    shape = _shape(table) if table is not None else None
    if shape is None:
        return None
    for key in document.order:
        if key == table_id:
            return None
        other = document.tables.get(key)
        if other is not None and _shape(other) == shape and other.rows != table.rows:
            return key
    return None
