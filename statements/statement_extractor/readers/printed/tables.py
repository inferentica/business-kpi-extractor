"""Printed statement tables as both formats yield them, HTML and plain text: each row's label, its indentation and
its figures by column position, and each column's header text. A figure lines up with the header above it however the
filer laid out "$", the number and its closing bracket.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...domain.basis import currencies_named, is_currency_word

# A figure may drop its leading zero: Coca-Cola's 1994 "$ .59" a share.
_NUMBER = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+|(?=\.\d))(\.\d+)?\s*\)?$")


_DASH = re.compile(r"^[—–\-−]+$")


NOTE_MARK = re.compile(r"\s*(\(\d{1,2}\)|\([a-z]\)|\*+)$")


@dataclass
class Cell:
    start: int
    span: int
    text: str

    @property
    def end(self) -> int:
        return self.start + self.span


@dataclass
class Row:
    label: str
    level: int  # the physical column the label starts in
    figures: dict[int, float] = field(default_factory=dict)  # physical column -> figure as printed
    cells: list[Cell] = field(default_factory=list)

    @property
    def is_heading(self) -> bool:
        return not self.figures


@dataclass
class Table:
    rows: list[Row]
    header: list[list[Cell]]  # rows above the first row with figures
    context: str  # the text before the table (its title, scale)
    index: int  # the table's place in the document


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def number(text: str) -> float | None:
    """A printed figure: 1,234 | (1,234) | -1,234 | — (nothing, read as zero)."""
    text = text.replace("$", "").strip()
    if _DASH.match(text):
        return 0.0
    match = _NUMBER.match(text)
    if not match:
        return None
    value = float((match.group(1) + (match.group(2) or "")).replace(",", ""))
    negative = text.startswith("(") or re.match(r"^\(?\s*[-−–]", text) is not None
    return -value if negative else value


YEAR_ONLY = re.compile(r"^(19|20)\d{2}$")


# Words a header row prints over each column rather than a period: TSMC's management reports print "USD | NTD | % |
# NTD | % | … | Amount | %" under their dates.
_COLUMN_WORD = re.compile(r"^(.{0,12}%.{0,12}|amounts?|change|chg|move|yoy|qoq|notes?|sales|\(?(un)?audited\)?)$", re.I)
PERCENT_WORD = re.compile(r"%")  # "%", "% of Sales", "Inc(Dec)%"


def is_column_word(text: str) -> bool:
    return bool(_COLUMN_WORD.match(text.strip())) or is_currency_word(text) and text.strip() != "$"


def is_subheader(cells: list[Cell]) -> bool:
    """A row naming what each column holds (currencies, amounts, percentages), after a label cell at most."""
    words = [cell for cell in cells if cell.text and cell.start > 0]
    return len(words) > 1 and all(is_column_word(cell.text) for cell in words) and \
        any(currencies_named(cell.text) or PERCENT_WORD.search(cell.text) for cell in words)


COLUMN_TITLE = re.compile(r"\b(19|20)\d{2}\b|\b(months|weeks|year|years|quarter)\s+ended\b|\bended\b|"
                           r"^(\s*(first|second|third|fourth)\s+quarter|\s*(three|six|nine|twelve)\s+months)\b|"
                           r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|"
                           r"\b\d{1,2}/\d{1,2}/(\d{2}|\d{4})\b|"  # PepsiCo's "3/21/09"
                           r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*-\d{2}\b", re.I)  # TSMC's "Sep-99"


def is_header_figures(row: Row) -> bool:
    """A row whose only figures are years printed bare ("2007 | 2006 | 2005", no comma): a header, not figures."""
    texts = {cell.start: cell.text for cell in row.cells}
    return bool(row.figures) and all(YEAR_ONLY.match(texts.get(column, "").strip("()$ ")) for column in row.figures)


def header_text(table: Table, column: int) -> str:
    """Everything printed above a figure's column, top to bottom: in each header row, the cell covering it, else the
    nearest cell within two columns (a year printed over the "$" beside its figure)."""
    parts = []
    for cells in table.header:
        covering = [cell for cell in cells if cell.start <= column < cell.end and not (cell is cells[0] and cell.start == 0
                                                                                        and len(cells) > 1)]
        if not covering:
            # Not the row's label cell; on a tie, the cell to the left (over the "$" before the figure). TSMC prints
            # "2002" one column left of its figures and "2003" from one column left of its two: the figure between
            # them is 2002's.
            near = [(min(abs(cell.start - column), abs(cell.end - 1 - column)), cell.end - 1 >= column, cell)
                    for cell in cells if cell.text and not (cell is cells[0] and cell.start == 0 and len(cells) > 1)]
            near = [item for item in near if item[0] <= 2]
            covering = [min(near, key=lambda item: item[:2])[2]] if near else []
        parts.extend(cell.text for cell in covering)
    return " ".join(parts)


def figure_columns(table: Table) -> list[tuple[int, ...]]:
    """The table's figure columns, left to right, each as the physical columns its figures sit in: figures printed one
    cell apart (after a "$" cell on some rows, not on others) under one header cell, or side by side, are one column."""
    counts: dict[int, int] = {}
    for row in table.rows:
        for column in row.figures:
            counts[column] = counts.get(column, 0) + 1
    if not counts:
        return []
    most = max(counts.values())
    positions = sorted(column for column, count in counts.items() if count >= 2 or most < 3)
    # A "Notes" column's references go before grouping: a currency row spanning it and the next column ("NT$" over
    # both in TSMC's FY2001 20-F) would merge them.
    notes = {position for position in positions for cells in table.header for cell in cells
             if cell.start <= position < cell.end and re.fullmatch(r"notes?", cell.text.strip(), re.I)}
    positions = [position for position in positions if position not in notes]
    # A row naming each column's currency sits lowest and splits a year's two columns, its own and a translation
    # (TSMC's "2003" over "NT$ | US$"); else the lowest row with years or spans.
    bottom = next((cells for cells in reversed(table.header) if (money := [cell for cell in cells if cell.start > 0])
                   and (all(currencies_named(cell.text) for cell in money) or is_subheader(cells))),
                  None) or next((cells for cells in reversed(table.header) if any(YEAR_ONLY.match(cell.text) or COLUMN_TITLE.search(cell.text)
                                                                                  for cell in cells[1:] or cells)), [])

    def under(position: int):
        return next(((cell.start, cell.end) for cell in bottom if cell.start <= position < cell.end and cell.text), None)

    groups: list[list[int]] = []
    for position in positions:
        if groups:
            last = groups[-1][-1]
            same_header = under(position) is not None and under(position) == under(last)
            if same_header or (position - last == 1 and under(position) is None):
                groups[-1].append(position)
                continue
        groups.append([position])
    # A "Notes" column holds note references, not figures (TSMC: "Cash and cash equivalents | 6 | 242,695.6").
    # Its header is the cell right over it; the rows above and below may reach it from the columns beside it.
    # A percentage column beside each amount (TSMC's "% of net sales") is no figure column either.
    groups = [group for group in groups if not any(cell.start <= group[0] < cell.end and (re.fullmatch(r"notes?", cell.text.strip(), re.I)
                                                                                          or PERCENT_WORD.search(cell.text))
                                                   for cells in table.header for cell in cells)]
    if not groups:
        return []
    # A group carrying few figures beside a full one is a stray cell (a footnote figure), not a column.
    sizes = [sum(counts.get(position, 0) for position in group) for group in groups]
    keep = max(sizes) * 0.3
    return [tuple(group) for group, size in zip(groups, sizes) if size >= keep]
