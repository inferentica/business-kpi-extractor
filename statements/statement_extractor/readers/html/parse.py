"""HTML tables as printed statements: each row's cells laid out on the table's physical columns (a cell spanning three
columns covers three), so a figure lines up with the header above it. A label's indentation is how far right its cell
starts. Filings before XBRL (about 2001-2008) print their statements this way.
"""
from __future__ import annotations

import re

import lxml.html

from ..printed.tables import COLUMN_TITLE, NOTE_MARK, Cell, Row, Table, clean, is_header_figures, number


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
    label = NOTE_MARK.sub("", " ".join(label_parts)).strip()
    label = re.sub(r"\s+\d{1,2}(\s*,\s*\d{1,2})+$|\s+\d{1,2},$", "", label)  # "NET SALES 2, 26" in one cell
    if not label and not figures:
        return None
    return Row(label=label, level=level if level is not None else 0, figures=figures, cells=cells)


def parse_html(html: str | bytes) -> list[Table]:
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
                if row.figures and not is_header_figures(row):
                    seen_figures = True
                    rows.append(row)
                elif not seen_figures:
                    texts = " ".join(cell.text for cell in cells)
                    if COLUMN_TITLE.search(texts):
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
