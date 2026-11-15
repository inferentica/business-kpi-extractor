"""Releases printed as slide images, their words hidden beneath each picture in white 1pt text (ASML's from its 2025
second quarter, made by Workiva): one run of words per slide, built into the printed table model.

A slide's header names its columns ("Three months ended Six months ended Jun 30, Jun 29, Jun 30, Jun 29, (unaudited, in
millions €) 2024 2025 2024 2025"), and may stand anywhere in the run (a cash flow slide prints it again over its
financing section). Every line after it is a label and one figure for each column; section headings run into the label
after them and are split off by their words. The slide's title ends the run, after its navigation.
"""
from __future__ import annotations

import html
import re

from ..printed.find import TITLE_PATTERNS
from ..printed.tables import Cell, Row, Table, number

_HIDDEN = re.compile(r"<FONT[^>]*font-size:\s*1pt[^>]*>(.*?)</FONT>", re.I | re.S)
_DAY = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?"
_HEADER = re.compile(r"((?:(?:three|six|nine|twelve)\s+months\s+ended|years?\s+ended)\s*)*"
                     r"((?:" + _DAY + r"\s*)+)\s*(\([^)]*\bin\s+(?:millions|thousands|billions)[^)]*\))\s*((?:(?:19|20)\d{2}\s+)+)", re.I)
_SPAN = re.compile(r"(?:three|six|nine|twelve)\s+months\s+ended|years?\s+ended", re.I)
_FIGURE = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$|^\(?-?\.\d+\)?$|^[—–-]$")
# Words after the last statement title that make the slide's own title another table.
_OTHER = re.compile(r"\b(ratios|other\s+data|notes|segment)\b", re.I)
# Headings that run into the label after them.
_HEADINGS = re.compile(r"^(assets|liabilities and (?:shareholders|stockholders)['’]? equity|"
                       r"cash flows (?:from|used in|provided by) (?:operating|investing|financing) activities)\s+", re.I)


def is_slides(content: str) -> bool:
    """A document whose words are hidden under slide images, with no tables of its own."""
    return "<table" not in content.lower() and len(_HIDDEN.findall(content)) >= 2


def parse_slides(content: str) -> list[Table]:
    tables = []
    for index, raw in enumerate(_HIDDEN.findall(content)):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
        table = _slide(text, index)
        if table is not None:
            tables.append(table)
    return tables


def _slide(text: str, index: int) -> Table | None:
    headers = list(_HEADER.finditer(text))
    if not headers:
        return None
    first = headers[0]
    dates = re.findall(_DAY, first.group(2))
    years = first.group(4).split()
    count = len(years)
    if count < 1 or len(dates) != count:
        return None
    body = text
    for match in reversed(headers):
        body = body[:match.start()] + " " + body[match.end():]
    rows = _rows(body.split(), count)
    if not rows:
        return None
    positions = [2 + 2 * column for column in range(count)]
    spans = _SPAN.findall(text[first.start():first.start(2)])
    header = []
    if spans and count % len(spans) == 0:
        width = count // len(spans)
        header.append([Cell(positions[n * width], 2 * width - 1, span) for n, span in enumerate(spans)])
    header.append([Cell(position, 1, day) for position, day in zip(positions, dates)])
    header.append([Cell(position, 1, year) for position, year in zip(positions, years)])
    table_rows = []
    for label, figures in rows:
        table_rows.append(Row(label=label, level=0, figures=dict(zip(positions, figures)) if figures else {},
                              cells=[Cell(0, 1, label)]))
    # The title ends the run, after the navigation that names every statement: "… Consolidated Balance Sheets
    # Consolidated Statements of Cash Flows Summary US GAAP Consolidated Statements of Operations".
    tail = text[-400:]
    titles = [match for pattern in TITLE_PATTERNS for match in pattern.finditer(tail)]
    if not titles:
        return None
    last = max(titles, key=lambda match: match.start())
    title = tail[last.start():last.end()]
    if _OTHER.search(tail[last.end():]):
        return None  # the slide's own title is no statement of the three ("Ratios and Other Data")
    return Table(rows=table_rows, header=header, context=f"{title} {first.group(3)}", index=index)


def _rows(words: list[str], count: int) -> list[tuple[str, list[float] | None]]:
    """Each label and its figures, in order; a heading run into the label after it becomes a row of its own."""
    rows, label, position = [], [], 0
    while position < len(words):
        run = position
        while run < len(words) and _FIGURE.match(words[run]):
            run += 1
        figures = words[position:run]
        if not figures:
            label.append(words[position])
            position += 1
            continue
        if len(figures) != count or not label:
            label.extend(figures)  # figures in a label, or a page number
            position = run
            continue
        text = " ".join(label)
        heading = _HEADINGS.match(text)
        if heading:
            rows.append((heading.group(1), None))
            text = text[heading.end():]
        if ":" in text:
            before, text = text.rsplit(":", 1)
            rows.append((before.strip() + ":", None))
        values = [number(figure) for figure in figures]
        if all(value is not None for value in values) and text.strip():
            rows.append((text.strip(), values))
        label, position = [], run
    return rows
