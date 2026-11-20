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
    commas = any(_COMMA_THOUSANDS.match(text) for text in texts)
    if any(_DOTTED_MILLIONS.match(text) for text in texts) and not commas:
        swap = str.maketrans(".,", ",.")
        return [[Cell(cell.start, cell.span, cell.text.translate(swap)) if _DOTTED_FIGURE.match(cell.text.strip()) else cell
                 for cell in cells] for cells in lines]
    return _dots_as_thousands(lines, texts, commas)


_DOT_GROUPS = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*[1-9]\d{0,2}(?:\.\d{3})+\s*\)?$")  # "639.406", never "0.125"
_ONE_DOT = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*[1-9]\d{0,2}\.\d{3}\s*\)?$")
_DECIMAL = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*[\d,]*\d\.(?:\d{1,2}|\d{4,})\s*\)?%?$")  # a decimal point: 2.97, 1.5, 0.1234


def _dots_as_thousands(lines: list[list[Cell]], texts: list[str], commas: bool) -> list[list[Cell]]:
    """Thousands parted by dots in a table printing no decimals: Eletropaulo's 2005 statements in dots throughout
    ("639.406", three of them at least), or a dot among commas (Azul's Q2 2017 "144.505", Light's "27.235", Ultrapar's
    "310.050"). A per-share row's figures keep their point (a dividend of 1.250 a share)."""
    from ...domain.printed_labels import PER_SHARE

    def label(cells):
        return next((cell.text for cell in cells if cell.text.strip() and not re.search(r"\d", cell.text)), "")
    if any(_DECIMAL.match(cell.text.strip()) for cells in lines if not PER_SHARE.search(label(cells)) for cell in cells):
        return lines  # a point among the amounts is a decimal point; per-share rows print theirs (Azul's EPS)
    # Among commas, a dot in place of one comma only ("144.505"): two dotted groups beside commas are two ways, left be.
    groups = _DOT_GROUPS if not commas else _ONE_DOT
    dotted = [text for text in texts if groups.match(text)]
    if not dotted or not commas and len(dotted) < 3:
        return lines
    out = []
    for cells in lines:
        out.append(cells if PER_SHARE.search(label(cells)) else [
            Cell(cell.start, cell.span, cell.text.replace(".", ",")) if groups.match(cell.text.strip()) else cell
            for cell in cells])
    return out
