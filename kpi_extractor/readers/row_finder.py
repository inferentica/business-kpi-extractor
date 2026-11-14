"""Finding a row read in an earlier quarter in this quarter's tables, when its label has changed with the side its figure
fell on, or it shows a dash for no figure."""

from __future__ import annotations

import re
from typing import Callable

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
