"""An amount read from a column of rates: State Street's average statement of condition pairs each quarter's average
balance with its rate (Loans: "32,035 | 2.08 | | 34,425 | 2.00"), and a place replayed from a layout with one empty cell
fewer read Q4 2021's average loans as 2.08 (the third quarter's rate) at its table's "in millions". A column whose
figures are mostly small decimals (its foot can hold a net interest income line) beside rows of larger amounts holds
rates, not amounts."""
from __future__ import annotations

from ..documents.document import Document, parse_number

_RATE_LIMIT = 100.0


def rate_column(document: Document | None, locator: dict) -> bool:
    """Whether the table cell a reading took, itself a decimal under 100, is in a column of rates (three or more of its
    figures, at least three in four, decimals under 100), while its own row prints amounts of a thousand or more."""
    table = document.tables.get(str(locator.get("table"))) if document is not None else None
    row, col = locator.get("row"), locator.get("col")
    if table is None or row is None or col is None or int(row) >= len(table.rows):
        return False
    row, col = int(row), int(col)
    column = []
    for cells in table.rows:
        text = cells[col].strip() if col < len(cells) else ""
        parsed = parse_number(text)
        if parsed is not None and not parsed.percent:
            column.append((text, parsed.value))
    own = table.rows[row][col].strip() if col < len(table.rows[row]) else ""
    small = sum(1 for text, value in column if "." in text and abs(value) < _RATE_LIMIT)
    rates = ("." in own and (parsed := parse_number(own)) is not None and abs(parsed.value) < _RATE_LIMIT
             and small >= 3 and small * 4 >= len(column) * 3)
    amounts = any((parsed := parse_number(cell)) is not None and not parsed.percent and abs(parsed.value) >= 1000
                  for at, cell in enumerate(table.rows[row]) if at != col)
    return rates and amounts
