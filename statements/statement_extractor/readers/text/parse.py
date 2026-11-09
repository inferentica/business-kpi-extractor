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

from ...domain.basis import is_currency_word
from ..printed.tables import COLUMN_TITLE, Cell, Row, Table, clean, number
from .blocks import BLOCK, TAG, tagged, untagged
from .lines import RULE, YEAR, Chunk, Line, chunks, split

_ALIGN = 3  # characters a figure can sit off its column
_SPAN_WORDS = re.compile(r"\b(months|weeks|year|quarter)s?\s+ended\b", re.I)


def _columns(lines: list[Line]) -> list[int]:
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
    found = chunks(line.replace("$", " "))
    if not found or not columns:
        return []
    left = [chunk for chunk in found if chunk.end < columns[0] - 12]
    over = [chunk for chunk in found if chunk not in left]
    cells = [Cell(0, 1, " ".join(chunk.text for chunk in left))] if left else []
    if len(over) == len(columns):
        # One word over each column, in order: Apple's FY1997 "1997" sits four characters off its column and "1996"
        # three, which nearest-word would give both columns to 1996.
        return cells + [Cell(column, 1, chunk.text) for column, chunk in zip(columns, over)]
    if len(over) > 1 and len(columns) % len(over) == 0 and all(_SPAN_WORDS.search(chunk.text) for chunk in over):
        # Spans over equal groups of columns, in order: ASML's "Three months ended December 31," over the first two
        # and "Twelve months ended December 31," over the last two, centred, so the second column sits nearer the next.
        width = len(columns) // len(over)
        return cells + [Cell(columns[n * width], columns[n * width + width - 1] - columns[n * width] + 1, chunk.text)
                        for n, chunk in enumerate(over)]
    owners: dict[int, list[int]] = {}
    for column in columns:
        def distance(chunk: Chunk) -> int:
            return 0 if chunk.start <= column <= chunk.end else min(abs(column - chunk.start), abs(column - chunk.end))
        if over:
            best = min(range(len(over)), key=lambda index: distance(over[index]))
            if distance(over[best]) <= 12:
                owners.setdefault(best, []).append(column)
    for index, owned in sorted(owners.items()):
        cells.append(Cell(min(owned), max(owned) - min(owned) + 1, over[index].text))
    return cells


_JOINING = re.compile(r"\b(and|or|of|for|in|on|to|the|before|after|from|less|plus|net|by|with|at|due)$|[,\-–]$", re.I)
# Words that open a continuation, never a label of its own ("Net", "Less" and "In" do start labels).
_LEADING_JOIN = re.compile(r"^(and|or|of|for|on|to|before|after|from|by|with|at|due)\b", re.I)


# A line in capitals that heads a section rather than starting a label ("CURRENT ASSETS", "INTEREST INCOME").
_CAPITAL_HEADING = re.compile(r"\b(ASSETS|LIABILITIES|EQUITY|ACTIVITIES|INCOME|EXPENSES?|REVENUES?|OPERATIONS)$")


def _capitals(text: str) -> bool:
    return any(character.isalpha() for character in text) and text.upper() == text


def _continues(before: str, after: str, already: bool, indented: bool = False) -> bool:
    """Whether a line without figures runs on into the line after it: the next line starts in lower case, with a
    bracket or a figure, or either side has a joining word ("INCOME BEFORE INCOME TAXES" / "AND CUMULATIVE EFFECT OF" /
    "ACCOUNTING CHANGE"), or a label in capitals goes on in capitals one step in, or after a joining word
    ("AVERAGE SHARES" / " OUTSTANDING"; " AND CHANGE IN ACCOUNTING" / " PRINCIPLE"). A heading ending in a colon never
    does, and a heading in capitals does not run into a label in mixed case ("INCOME PER SHARE" / "Before change in")."""
    if before.endswith(":"):
        return False
    if _capitals(before) and not _capitals(after) and after[:1].isupper():
        return False
    if _JOINING.search(before) or _LEADING_JOIN.match(after):
        return True
    heading = _CAPITAL_HEADING.search(before) and not re.search(r"\b(BEFORE|AFTER|FROM|PER)\b", before)
    if _capitals(before) and _capitals(after) and not heading and (
            indented or _LEADING_JOIN.match(before)):
        return True
    if already:
        return False
    return not after or after[:1].islower() or after[:1] in "(0123456789"


def _rows(lines: list[Line], columns: list[int]) -> list[Row]:
    rows: list[Row] = []
    pending: list[Line] = []

    def heading(line: Line) -> None:
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
        joined: list[str] = []
        indent = line.indent
        following, following_indent = label, line.indent
        while pending and _continues(pending[-1].label, following, bool(joined), following_indent > pending[-1].indent):
            previous = pending.pop()
            joined.insert(0, previous.label)
            indent = previous.indent  # the label starts where its first line does
            following, following_indent = previous.label, previous.indent
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
    return _wrapped_names(rows)


def _wrapped_names(rows: list[Row]) -> list[Row]:
    """A "heading" over a single line set further in, with the line after back at the heading's own place, is one label
    wrapped: Coca-Cola's "Coca-Cola Hellenic Bottling" / "  Company S.A.  1,066" between two other bottlers."""
    out: list[Row] = []
    for index, row in enumerate(rows):
        name = out[-1] if out and index and out[-1] is rows[index - 1] else None
        after = rows[index + 1] if index + 1 < len(rows) else None
        if name is not None and not name.figures and name.label and not name.label.endswith(":") \
                and not _capitals(name.label) and row.figures and row.label and after is not None and after.label \
                and row.level > name.level == after.level:
            out[-1] = Row(label=f"{name.label} {row.label}", level=name.level, figures=row.figures, cells=row.cells)
            continue
        out.append(row)
    return out



def _table(before: str, title_lines: list[str], header_lines: list[str], body: list[str], index: int) -> Table:
    parsed = [line for line in (split(raw_line) for raw_line in body if not RULE.match(raw_line)) if line]
    columns = _columns(parsed)
    header, leading = [], []
    for line in header_lines:
        if RULE.match(line):
            continue
        words = chunks(line)
        if COLUMN_TITLE.search(line) or any(YEAR.match(chunk.text) for chunk in words) or \
                len(words) > 1 and all(is_currency_word(chunk.text) for chunk in words):  # ASML's "EUR EUR EUR USD"
            cells = _header(line, columns)
            if cells:
                header.append(cells)
        else:
            leading.append(line)  # a heading printed in the header area ("LIABILITIES AND SHARE-OWNERS' EQUITY")
    leading_rows = [Line(len(line) - len(line.lstrip()), clean(line), []) for line in leading if line.strip()]
    rows = _rows(leading_rows + parsed, columns)
    context = clean(TAG.sub(" ", before + " " + " ".join(title_lines)))
    return Table(rows=rows, header=header, context=context[-600:], index=index)


def parse_text(text: str) -> list[Table]:
    """Every table of a plain-text document, tagged <TABLE> blocks and untagged statements, in document order, each with
    the text before it (its title and scale)."""
    # A tab stands for the spaces to the next stop of eight: Apple's 1996-97 10-Qs end some wrapped labels with tabs,
    # which would put the figure after them in no column.
    text = "\n".join(line.expandtabs(8) for line in text.split("\n"))
    spans = [(block.start(), block.end()) for block in BLOCK.finditer(text)]
    parts = sorted([*tagged(text), *untagged(text, spans)], key=lambda part: part[0])
    return [_table(before, title, header, body, index) for index, (_start, before, title, header, body) in enumerate(parts)]
