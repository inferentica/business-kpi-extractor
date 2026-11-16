"""A page laid out as text placed at coordinates (a PDF converted to HTML: UBS's quarterly reports hold no <table>, only
15,000 boxes placed by "left" and "top") has its tables rebuilt from the positions, so every reader sees rows and
columns as in any other filing."""
from __future__ import annotations

import re

from lxml import etree, html

MIN_POSITIONED = 200
_LEFT = re.compile(r"left:\s*(-?[\d.]+)px", re.I)
_TOP = re.compile(r"top:\s*(-?[\d.]+)px", re.I)
_SAME_LINE = 2.5     # px: boxes this close in height sit on one line
_TABLE_GAP = 30.0    # px: a larger gap between figure lines ends a table
_COLUMN = 24.0       # px: figures this close horizontally (right-aligned, so their left edges vary) share a column
_FIGURE = re.compile(r"^\(?[-–—+]?\$?\s*\d[\d,.]*\s*%?\)?\s*%?$|^[-–—]$")


def rebuild_positioned(root) -> None:
    placed = root.xpath("//*[contains(translate(@style,'ABSOLUTE','absolute'),'absolute')]")
    pages: dict[object, list[tuple[float, float, str]]] = {}
    order: list[object] = []
    for element in placed:
        if any("absolute" in (ancestor.get("style") or "").lower() for ancestor in element.iterancestors()):
            continue  # a box inside a box: its text is its parent's
        style = element.get("style") or ""
        left, top = _LEFT.search(style), _TOP.search(style)
        text = re.sub(r"\s+", " ", element.text_content()).strip()
        if not (left and top and text):
            continue
        page = element.getparent()
        if page not in pages:
            pages[page] = []
            order.append(page)
        pages[page].append((float(top.group(1)), float(left.group(1)), text))
    for page in order:
        boxes = sorted(pages[page])
        lines: list[list[tuple[float, float, str]]] = []
        for box in boxes:
            if lines and abs(box[0] - lines[-1][0][0]) <= _SAME_LINE:
                lines[-1].append(box)
            else:
                lines.append([box])
        for line in lines:
            line.sort(key=lambda box: box[1])
        for child in list(page):
            if "absolute" in (child.get("style") or "").lower():
                page.remove(child)
        for element in _layout_elements(lines):
            page.append(element)


def _layout_elements(lines: list[list[tuple[float, float, str]]]) -> list:
    """Runs of lines with two or more figures become a table (with the heading lines just above), the rest paragraphs."""
    figures = [sum(1 for box in line[1:] if _FIGURE.match(box[2])) >= 2 for line in lines]
    out, index, paragraph = [], 0, []

    def flush_paragraph() -> None:
        if paragraph:
            element = html.Element("p")
            element.text = " ".join(box[2] for line in paragraph for box in line)
            out.append(element)
            paragraph.clear()

    while index < len(lines):
        if not figures[index]:
            paragraph.append(lines[index])
            index += 1
            continue
        start = index
        while index < len(lines) and (figures[index] or (index + 1 < len(lines) and figures[index + 1]
                                                         and lines[index + 1][0][0] - lines[index][0][0] <= _TABLE_GAP)) \
                and (index == start or lines[index][0][0] - lines[index - 1][0][0] <= _TABLE_GAP):
            index += 1
        # Heading lines just above the first figure line, right of the labels (dates, "Q3 2026"), head the columns.
        heads = []
        while paragraph and len(heads) < 3 and lines[start][0][0] - paragraph[-1][0][0] <= _TABLE_GAP * (len(heads) + 1) \
                and len(paragraph[-1]) >= 2:
            heads.insert(0, paragraph.pop())
        flush_paragraph()
        out.append(_table_element(heads, lines[start:index]))
    flush_paragraph()
    return out


_FOOTNOTE_MARK = re.compile(r"^\d$")
_CHAR_WIDTH = 5.0    # px: an average figure character in these converted pages (9-10 pt condensed type)
_RIGHT_EDGE = 14.0   # px: right edges this close share a column


def _right_edge(box: tuple[float, float, str]) -> float:
    return box[1] + _CHAR_WIDTH * len(box[2])


def _table_element(heads: list, body: list):
    # Footnote marks (a lone digit set beside a figure or a label) are no figures: columns come from the others.
    values = [box for line in body for box in line[1:] if _FIGURE.match(box[2]) and not _FOOTNOTE_MARK.match(box[2])] \
        or [box for line in body for box in line[1:] if _FIGURE.match(box[2])]
    first = min(box[1] for box in values)
    # Figures are right-aligned ("13,350" and "114" end together and start 20 px apart), so columns are found by where
    # each figure ends, estimated from its length; a column spans at most _RIGHT_EDGE (chaining merged neighbours).
    columns: list[list[float]] = []
    for right in sorted(_right_edge(box) for box in values):
        if columns and right - columns[-1][0] <= _RIGHT_EDGE:
            columns[-1].append(right)
        else:
            columns.append([right])
    centres = [sum(column) / len(column) for column in columns]
    table = html.Element("table")
    for line in [*heads, *body]:
        cells = [""] * (len(centres) + 1)
        label = [box[2] for box in line if box[1] < first - 8]
        cells[0] = " ".join(label)
        for box in line:
            if box[1] < first - 8:
                continue
            column = min(range(len(centres)), key=lambda i: abs(centres[i] - _right_edge(box)))
            if _FOOTNOTE_MARK.match(box[2]) and line in body and abs(centres[column] - _right_edge(box)) > _RIGHT_EDGE:
                continue  # a footnote mark between columns
            if _FOOTNOTE_MARK.match(box[2]) and cells[column + 1] and line in body:
                continue  # a footnote mark beside its figure ("585 2")
            cells[column + 1] = (cells[column + 1] + " " + box[2]).strip()
        row = etree.SubElement(table, "tr")
        for cell in cells:
            etree.SubElement(row, "td").text = cell
    return table
