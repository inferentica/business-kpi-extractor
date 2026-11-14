"""Finding a row read in an earlier quarter in this quarter's tables, when its label has changed with the side its figure
fell on, or it shows a dash for no figure."""

from __future__ import annotations

import re
from typing import Callable

from ..ai import Locator, TableLocator
from ..documents.document import Document, Table, parse_number
from ..rules.identity import sideless_key
from ..xbrl import member_key
from .extract import _row_label

NIL = re.compile(r"^\s*\$?\s*[—–-]+\s*$")


def row_finder(table: Table, col: int) -> Callable[[str], int | None]:
    """The row a label names in a table, among rows with a figure or a dash in the column (Wells Fargo's "Net gains from
    debt securities", "—" in some quarters): by its exact label, else by its label with the side of a gain or loss left
    out where only one row has it (rules/identity.py, sideless_key)."""
    exact: dict[str, int] = {}
    sided: dict[str, list[int]] = {}
    for index, row in enumerate(table.rows):
        cell = table.cell(index, col) or ""
        if parse_number(cell) is not None or NIL.match(cell):
            label = _row_label(row, col)
            exact.setdefault(member_key(label), index)
            sided.setdefault(sideless_key(label), []).append(index)

    def at(label: str) -> int | None:
        if member_key(label) in exact:
            return exact[member_key(label)]
        found = sided.get(sideless_key(label), [])
        return found[0] if len(found) == 1 else None
    return at


def shows_figure(document: Document, locator: dict) -> bool:
    """Whether this filing gives a figure on a row an earlier quarter read (in its column, in any table): a row that
    shows only a dash, or is gone, reports nothing this quarter. A place without a row label (a quoted sentence) is
    assumed to, and replay looks for it."""
    label, col = locator.get("row_label"), locator.get("col")
    if not label or col is None:
        return True
    for table in document.tables.values():
        row = row_finder(table, int(col))(label)
        if row is not None and parse_number(table.cell(row, int(col)) or "") is not None:
            return True
    return False


_TOTAL = re.compile(r"\btotal\b", re.I)


def table_from_rows(document: Document, group: str, parts: list[Locator]) -> TableLocator | None:
    """A breakdown read row by row whose rows do not add up to a total the table states, read as its whole table: from
    its first row down to the first row named a total that every figure between adds up to, to the unit (Wells Fargo's
    noninterest income, whose "Lease income" row is not on the list: its listed rows add up to no figure in the table).
    None unless every row was read from one table and column and that total is found."""
    if len(parts) < 2 or len({(part.table, part.col) for part in parts}) != 1 or any(part.row is None for part in parts):
        return None
    table, col = document.tables.get(parts[0].table or ""), parts[0].col
    if table is None or col is None:
        return None
    first, last = min(part.row for part in parts), max(part.row for part in parts)
    figures = [parse_number(table.cell(index, col) or "") for index in range(len(table.rows))]
    for row in range(last + 1, len(table.rows)):
        figure = figures[row]
        if figure is None:
            continue
        whole = sum(item.value for item in figures[first:row] if item is not None)
        if _TOTAL.search(_row_label(table.rows[row], col)) and abs(figure.value - whole) <= 0.5:
            return TableLocator(group=group, table=table.id, col=col, first_row=first, last_row=row - 1, total_row=row,
                                scale=parts[0].scale, scale_text=parts[0].scale_text)
    return None


def shows_dash(document: Document, locator: dict) -> bool:
    """Whether an earlier quarter's row is in this filing with a dash for its figure, in its column (Wells Fargo's "Net
    gains (losses) from debt securities", "(25)" in Q1 2024 and "—" in Q2): reported as nothing this quarter."""
    label, col = locator.get("row_label"), locator.get("col")
    if not label or col is None:
        return False
    for table in document.tables.values():
        row = row_finder(table, int(col))(label)
        if row is not None and NIL.match(table.cell(row, int(col)) or ""):
            return True
    return False
