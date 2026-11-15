"""A table row's cells at their column positions, as a browser lays them out: colspans widen a cell, and a cell with a
rowspan holds its columns in the rows below it too."""
from __future__ import annotations

import re

from ..printed.tables import Cell, clean


def _text(element) -> str:
    try:
        return clean(element.text_content())
    except ValueError:  # a comment or processing instruction
        return ""


def _cells(tr, held: dict[int, list[int]] | None = None) -> list[Cell]:
    """A row's cells at their columns, past those a cell above still holds with its rowspan (`held`: column -> [rows
    left, width]): Enel Chile's 6-Ks head the label and "Note" with rowspan="3", so the dates below start after them."""
    cells, position = [], 0
    above = {column: width for column, (left, width) in (held or {}).items() if left > 0}
    added: dict[int, list[int]] = {}
    for td in tr:
        if not isinstance(td.tag, str) or td.tag not in ("td", "th"):
            continue
        while position in above:
            position += above[position]
        try:
            span = max(1, int(td.get("colspan") or 1))
        except ValueError:
            span = 1
        try:
            rows = min(50, max(1, int(td.get("rowspan") or 1)))
        except ValueError:
            rows = 1
        cells.append(Cell(position, span, _text(td)))
        if rows > 1:
            added[position] = [rows - 1, span]
        position += span
    if held is not None:
        for column in list(held):
            held[column][0] -= 1
            if held[column][0] <= 0:
                del held[column]
        held.update(added)
    return cells


def _spanned(trs) -> list[list[Cell]]:
    """Each row's cells, rowspans carried down."""
    held: dict[int, list[int]] = {}
    return [_cells(tr, held) for tr in trs]


# "9.520.238", "1.044,0" (Embraer's 2011 releases): only thousands and a decimal comma read so.
_DOTTED_MILLIONS = re.compile(r"^\(?\s*[-−–]?\s*\d{1,3}(?:(?:\.\d{3}){2,}(?:,\d+)?|(?:\.\d{3})+,\d+)\s*\)?$")
_COMMA_THOUSANDS = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*\)?$")
_DOTTED_FIGURE = re.compile(r"^\(?\s*[-−–]?\s*\d{1,3}(?:\.\d{3})*(?:,\d+)?\s*\)?%?$")


def dotted_thousands(lines: list[list[Cell]]) -> list[list[Cell]]:
    """A table printing its thousands with dots and its decimals with commas, Brazil's way (Copel's Q3 2008 ITR:
    "9.520.238" over "639.406"), in the readers' figures: "9,520,238", "639,406". Only where a figure of two dotted
    groups shows the table's way and no figure prints commas for thousands."""
    texts = [cell.text.strip() for cells in lines for cell in cells]
    if not any(_DOTTED_MILLIONS.match(text) for text in texts) or any(_COMMA_THOUSANDS.match(text) for text in texts):
        return lines
    swap = str.maketrans(".,", ",.")
    return [[Cell(cell.start, cell.span, cell.text.translate(swap)) if _DOTTED_FIGURE.match(cell.text.strip()) else cell
             for cell in cells] for cells in lines]
