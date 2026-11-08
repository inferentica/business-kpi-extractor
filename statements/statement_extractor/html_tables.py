"""HTML tables as printed statements read them: each row's label, its indentation and its figures by column, and each
column's header text. Filings before XBRL (about 2001-2008) print their statements this way.

A row's cells are laid out on the table's physical columns (a cell spanning three columns covers three), so a figure
lines up with the header above it however the filer split "$", the number and its closing bracket into cells. A
label's indentation is how far right its cell starts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import lxml.html

_NUMBER = re.compile(r"^\(?\s*[-−–]?\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*\)?$")
_DASH = re.compile(r"^[—–\-−]+$")
_NOTE_MARK = re.compile(r"\s*(\(\d{1,2}\)|\([a-z]\)|\*+)$")


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


def _text(element) -> str:
    try:
        return clean(element.text_content())
    except ValueError:  # a comment or processing instruction
        return ""


def _cells(tr) -> list[Cell]:
    cells, position = [], 0
    for td in tr:
        if not isinstance(td.tag, str) or td.tag not in ("td", "th"):
            continue
        try:
            span = max(1, int(td.get("colspan") or 1))
        except ValueError:
            span = 1
        cells.append(Cell(position, span, _text(td)))
        position += span
    return cells


def _row(cells: list[Cell]) -> Row | None:
    """A row's label (the text before its first figure) and figures. A closing bracket in a cell of its own makes the
    figure before it negative; "$" and "%" cells are not figures."""
    label_parts, figures, level = [], {}, None
    last_figure = None
    for cell in cells:
        text = cell.text
        if not text:
            continue
        if text in (")", ")%") and last_figure is not None:
            figures[last_figure] = -abs(figures[last_figure])
            continue
        if text in ("$", "%", "€", "NT$", "US$") or text.endswith("%") and number(text.rstrip("%")) is not None:
            continue
        value = number(text.rstrip(")")) if text.endswith(")") and not text.startswith("(") else number(text)
        if value is not None and not label_parts and re.fullmatch(r"\d{1,2}", text):
            continue  # a note reference, not a figure
        if value is not None and (figures or label_parts):
            if text.endswith(")") and not text.startswith("("):
                value = -abs(value)
            figures[cell.start] = value
            last_figure = cell.start
            continue
        if text.startswith("(") and not text.endswith(")") and number(text + ")") is not None and (figures or label_parts):
            figures[cell.start] = -abs(number(text + ")"))  # "(1,234" with its bracket in the next cell
            last_figure = cell.start
            continue
        if figures:
            continue  # text after the figures (a footnote mark)
        if re.fullmatch(r"\d{1,2}(\s*,\s*\d{1,2})*", text):
            continue  # a note reference before or after the label (ASML's and TSMC's "Notes" columns)
        if level is None:
            level = cell.start
        label_parts.append(text)
    label = _NOTE_MARK.sub("", " ".join(label_parts)).strip()
    label = re.sub(r"\s+\d{1,2}(\s*,\s*\d{1,2})+$|\s+\d{1,2},$", "", label)  # "NET SALES 2, 26" in one cell
    if not label and not figures:
        return None
    return Row(label=label, level=level if level is not None else 0, figures=figures, cells=cells)


def tables(html: str | bytes) -> list[Table]:
    """Every table of a document with rows, its header rows and the text before it."""
    raw = html.encode("utf-8") if isinstance(html, str) else html
    tree = lxml.html.fromstring(raw)
    found = []
    previous_text: list[str] = []
    for index, element in enumerate(tree.iter()):
        if not isinstance(element.tag, str):
            continue
        if element.tag == "table":
            if element.getparent() is not None and any(a.tag == "table" for a in element.iterancestors()):
                continue
            rows, header, seen_figures = [], [], False
            for tr in element.iter("tr"):
                cells = _cells(tr)
                row = _row(cells)
                if row is None:
                    continue
                if row.figures and not _is_header_figures(row):
                    seen_figures = True
                    rows.append(row)
                elif not seen_figures:
                    texts = " ".join(cell.text for cell in cells)
                    if _COLUMN_TITLE.search(texts):
                        header.append([cell for cell in cells if cell.text])
                    elif row.label:
                        rows.append(Row(label=row.label, level=row.level, cells=cells))
                else:
                    rows.append(row)
            context = " ".join(previous_text)[-600:]
            found.append(Table(rows=rows, header=header, context=context, index=len(found)))
            previous_text = []
        elif element.tag in ("p", "div", "font", "b", "center", "h1", "h2", "h3", "h4", "span", "td") and \
                not any(a.tag == "table" for a in element.iterancestors()):
            if element.text and element.text.strip():
                previous_text.append(clean(element.text))
            for child in element:
                if child.tail and child.tail.strip():
                    previous_text.append(clean(child.tail))
            previous_text = previous_text[-40:]
    return found


_YEAR_ONLY = re.compile(r"^(19|20)\d{2}$")
_COLUMN_TITLE = re.compile(r"\b(19|20)\d{2}\b|\b(months|weeks|year|years|quarter)\s+ended\b|\bended\b|"
                           r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b", re.I)


def _is_header_figures(row: Row) -> bool:
    """A row whose only figures are years printed bare ("2007 | 2006 | 2005", no comma): a header, not figures."""
    texts = {cell.start: cell.text for cell in row.cells}
    return bool(row.figures) and all(_YEAR_ONLY.match(texts.get(column, "").strip("()$ ")) for column in row.figures)


def header_text(table: Table, column: int) -> str:
    """Everything printed above a figure's column, top to bottom: in each header row, the cell covering it, else the
    nearest cell within two columns (a year printed over the "$" beside its figure)."""
    parts = []
    for cells in table.header:
        covering = [cell for cell in cells if cell.start <= column < cell.end and not (cell is cells[0] and cell.start == 0
                                                                                        and len(cells) > 1)]
        if not covering:
            near = [(min(abs(cell.start - column), abs(cell.end - 1 - column)), cell) for cell in cells[1:] if cell.text]
            near = [item for item in near if item[0] <= 2]
            covering = [min(near, key=lambda item: item[0])[1]] if near else []
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
    bottom = next((cells for cells in reversed(table.header) if any(_YEAR_ONLY.match(cell.text) or _COLUMN_TITLE.search(cell.text)
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
    # A group carrying few figures beside a full one is a stray cell (a footnote figure), not a column.
    sizes = [sum(counts.get(position, 0) for position in group) for group in groups]
    keep = max(sizes) * 0.3
    return [tuple(group) for group, size in zip(groups, sizes) if size >= keep]
