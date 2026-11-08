"""Plain-text tables as printed statements (EDGAR's SGML filings of about 1993-2000): each <TABLE> block's rows with
their labels, indentation and figures by column, and each column's header text.

Columns are character positions: figures are right-aligned, so a column is where its figures end, and a header word
belongs to the column it sits over ("THREE MONTHS ENDED" spans the two columns under it). A label can wrap: a line
without figures runs on into the next one ("Interest and other income" / "(expense), net  (50)"), and a line that
starts in lower case finishes the label above it ("Selling, general and  386" / "administrative"). A line of figures
with no label is a subtotal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..printed.tables import COLUMN_TITLE, NOTE_MARK, Cell, Row, Table, clean, number

_BLOCK = re.compile(r"<TABLE>(.*?)</TABLE>", re.I | re.S)
_TAG = re.compile(r"</?(S|C|CAPTION|PAGE|FN|F\d+|TABLE)>", re.I)
_CHUNK = re.compile(r"\S+(?: \S+)*")  # words one space apart; two or more spaces part chunks
_FIGURE = re.compile(r"^\(?-?\d[\d,]*(\.\d+)?\)?$|^\(?-?\.\d+\)?$|^-+$|^—$")
_PERCENT = re.compile(r"^\(?-?[\d.,]+\)?%$")
_RULE = re.compile(r"^[\s\-=_]*$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_ALIGN = 3  # characters a figure can sit off its column


@dataclass
class _Chunk:
    start: int
    end: int
    text: str


@dataclass
class _Line:
    indent: int
    label: str
    figures: list[_Chunk]


def _chunks(line: str) -> list[_Chunk]:
    return [_Chunk(match.start(), match.end(), match.group(0)) for match in _CHUNK.finditer(line)]


def _split(line: str) -> _Line | None:
    """A line's label and the run of figures that ends it ("Net sales  $ 9,188,748  $ 7,976,954")."""
    chunks = _chunks(line.replace("$", " "))
    if not chunks:
        return None
    run = len(chunks)
    while run > 0 and (_FIGURE.match(chunks[run - 1].text) or _PERCENT.match(chunks[run - 1].text)):
        run -= 1
    label_chunks, figures = chunks[:run], [chunk for chunk in chunks[run:] if not _PERCENT.match(chunk.text)]
    label = " ".join(chunk.text for chunk in label_chunks)
    label = NOTE_MARK.sub("", re.sub(r"\s*\.{2,}[\s.]*$", "", label)).strip()  # dot leaders: "Net sales........"
    return _Line(chunks[0].start, clean(label), figures)


def _columns(lines: list[_Line]) -> list[int]:
    """Where each figure column ends: the right edges of figures, gathered where they lie within a few characters."""
    edges = sorted(chunk.end for line in lines for chunk in line.figures)
    groups: list[list[int]] = []
    for edge in edges:
        if groups and edge - groups[-1][-1] <= 2:
            groups[-1].append(edge)
        else:
            groups.append([edge])
    most = max((len(group) for group in groups), default=0)
    return [max(group) for group in groups if len(group) >= max(2, most * 0.3)]


def _nearest(columns: list[int], edge: int) -> int | None:
    best = min(columns, key=lambda column: abs(column - edge), default=None)
    return best if best is not None and abs(best - edge) <= _ALIGN else None


def _header(line: str, columns: list[int]) -> list[Cell]:
    """A header line's words as cells over the columns they name: one word per column where there are as many, else
    each column takes the word nearest above it (a span over several columns)."""
    chunks = _chunks(line.replace("$", " "))
    if not chunks or not columns:
        return []
    left = [chunk for chunk in chunks if chunk.end < columns[0] - 12]
    over = [chunk for chunk in chunks if chunk not in left]
    cells = [Cell(0, 1, " ".join(chunk.text for chunk in left))] if left else []
    owners: dict[int, list[int]] = {}
    for column in columns:
        def distance(chunk: _Chunk) -> int:
            return 0 if chunk.start <= column <= chunk.end else min(abs(column - chunk.start), abs(column - chunk.end))
        if over:
            best = min(range(len(over)), key=lambda index: distance(over[index]))
            if distance(over[best]) <= 12:
                owners.setdefault(best, []).append(column)
    for index, owned in sorted(owners.items()):
        cells.append(Cell(min(owned), max(owned) - min(owned) + 1, over[index].text))
    return cells


def _rows(lines: list[_Line], columns: list[int]) -> list[Row]:
    rows: list[Row] = []
    pending: list[_Line] = []

    def heading(line: _Line) -> None:
        rows.append(Row(label=line.label, level=line.indent, cells=[Cell(line.indent, 1, line.label)]))

    for line in lines:
        if not line.figures:
            continued = rows and rows[-1].figures and not pending and line.label[:1].islower()
            if continued:
                rows[-1].label = f"{rows[-1].label} {line.label}"  # "Selling, general and" / "administrative"
            else:
                pending.append(line)
            continue
        label = line.label
        joins = not label or label[:1].islower() or label[:1] in "(0123456789"
        joined: list[str] = []
        indent = line.indent
        while pending and joins:
            previous = pending[-1]
            if previous.label.endswith(":") or (previous.label.isupper() and joined):
                break
            joined.insert(0, pending.pop().label)
            indent = previous.indent  # the label starts where its first line does
            joins = not previous.label.isupper()
        for waiting in pending:
            heading(waiting)
        pending = []
        figures, cells = {}, [Cell(line.indent, 1, label)] if label or joined else []
        for chunk in line.figures:
            column = _nearest(columns, chunk.end)
            value = number(chunk.text)
            if column is not None and value is not None:
                figures[column] = value
                cells.append(Cell(column, 1, chunk.text))
        full = " ".join([*joined, label]).strip()
        rows.append(Row(label=full, level=indent, figures=figures, cells=cells))
    for waiting in pending:
        heading(waiting)
    return rows


def parse_text(text: str) -> list[Table]:
    """Every <TABLE> block of a plain-text document as a table, with the text before it (its title and scale)."""
    found: list[Table] = []
    previous_end = 0
    for block in _BLOCK.finditer(text):
        before = text[max(previous_end, block.start() - 1500):block.start()]
        previous_end = block.end()
        raw = block.group(1).splitlines()
        caption = next((index for index, line in enumerate(raw) if re.search(r"<CAPTION>", line, re.I)), None)
        marker = next((index for index, line in enumerate(raw) if re.search(r"<[SC]>", line, re.I)), None)
        title_lines = raw[:caption] if caption is not None else []
        header_start = caption + 1 if caption is not None else 0
        header_end = marker if marker is not None and marker >= header_start else header_start
        body = [_TAG.sub(" ", line) for line in raw[header_end:]]
        parsed = [line for line in (_split(raw_line) for raw_line in body if not _RULE.match(raw_line)) if line]
        columns = _columns(parsed)
        header_lines = [_TAG.sub(" ", line) for line in raw[header_start:header_end] if not _RULE.match(_TAG.sub(" ", line))]
        header, leading = [], []
        for line in header_lines:
            if COLUMN_TITLE.search(line) or any(_YEAR.match(chunk.text) for chunk in _chunks(line)):
                cells = _header(line, columns)
                if cells:
                    header.append(cells)
            else:
                leading.append(line)  # a heading printed in the header area ("LIABILITIES AND SHARE-OWNERS' EQUITY")
        leading_rows = [_Line(len(line) - len(line.lstrip()), clean(line), []) for line in leading if line.strip()]
        rows = _rows(leading_rows + parsed, columns)
        context = clean(_TAG.sub(" ", before + " " + " ".join(title_lines)))
        found.append(Table(rows=rows, header=header, context=context[-600:], index=len(found)))
    return found
