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


def not_a_percent(table: Table, row: int, col: int, text: str) -> str | None:
    """Why a cell with no % sign is not a percent, or None when it is marked as one where it sits: its row, its own
    column's heading, the table's heading over its labels (Coca-Cola's "Percent Change"), or the heading over its row
    (PepsiCo's "Impact on % Change of:"). Not other columns' headings: Wells Fargo heads a "% Change from" column over
    every table, and its Consumer Banking "Total loans" ($335,644 million) was taken for allowance coverage (1.40%).
    A cell printed with a currency sign is an amount."""
    if re.search(r"[$€£¥₩₹]", text):
        return f"{text!r} is an amount, not a percentage"
    first = next((i for i, cells in enumerate(table.rows) if cells and re.search(r"[A-Za-z]", cells[0])
                  and any(parse_number(cell) for cell in cells[1:])), 1)
    heading = " ".join(cells[index] for cells in table.rows[:max(first, 1)][:6] for index in {0, col} if index < len(cells))
    where = f"{' '.join(table.rows[row])} {heading} {_section_heading(table, row)}"
    return None if re.search(r"%|\bpercent", where, re.I) else f"{text!r} is not a percentage"


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
