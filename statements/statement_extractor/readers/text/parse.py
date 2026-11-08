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


def _figures_apart(chunks: list[_Chunk]) -> list[_Chunk]:
    """A chunk of figures one space apart is a figure each: Apple's 1994 "(257,228) (1,359,796)"."""
    out = []
    for chunk in chunks:
        parts = list(re.finditer(r"\S+", chunk.text))
        if len(parts) > 1 and all(_FIGURE.match(part.group(0)) for part in parts):
            out.extend(_Chunk(chunk.start + part.start(), chunk.start + part.end(), part.group(0)) for part in parts)
        else:
            out.append(chunk)
    return out


def _split(line: str) -> _Line | None:
    """A line's label and the run of figures that ends it ("Net sales  $ 9,188,748  $ 7,976,954")."""
    chunks = _figures_apart(_chunks(line.replace("$", " ")))
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
    if len(over) == len(columns):
        # One word over each column, in order: Apple's FY1997 "1997" sits four characters off its column and "1996"
        # three, which nearest-word would give both columns to 1996.
        return cells + [Cell(column, 1, chunk.text) for column, chunk in zip(columns, over)]
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


_UNTAGGED_TITLE = re.compile(r"^[ \t]*(condensed\s+)?(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?"
                             r"(income|operations|earnings|cash\s+flows?|financial\s+(position|condition)))\b", re.I | re.M)
_UNTAGGED_END = re.compile(r"^\s*(see\s+(accompanying\s+)?notes|the\s+accompanying\s+notes)|<PAGE>|<TABLE>", re.I)


def _table(before: str, title_lines: list[str], header_lines: list[str], body: list[str], index: int) -> Table:
    parsed = [line for line in (_split(raw_line) for raw_line in body if not _RULE.match(raw_line)) if line]
    columns = _columns(parsed)
    header, leading = [], []
    for line in header_lines:
        if _RULE.match(line):
            continue
        if COLUMN_TITLE.search(line) or any(_YEAR.match(chunk.text) for chunk in _chunks(line)):
            cells = _header(line, columns)
            if cells:
                header.append(cells)
        else:
            leading.append(line)  # a heading printed in the header area ("LIABILITIES AND SHARE-OWNERS' EQUITY")
    leading_rows = [_Line(len(line) - len(line.lstrip()), clean(line), []) for line in leading if line.strip()]
    rows = _rows(leading_rows + parsed, columns)
    context = clean(_TAG.sub(" ", before + " " + " ".join(title_lines)))
    return Table(rows=rows, header=header, context=context[-600:], index=index)


def _tagged(text: str):
    """(position, before, title lines, header lines, body lines) for each <TABLE> block."""
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
        yield (block.start(), before, title_lines, [_TAG.sub(" ", line) for line in raw[header_start:header_end]],
               [_TAG.sub(" ", line) for line in raw[header_end:]])


def _untagged(text: str, spans: list[tuple[int, int]]):
    """Statements printed with no <TABLE> tags (Coca-Cola's 10-Qs of 2002-2004): from a statement's title line to the
    page's end, the lines before the first figures being its title and header."""
    previous_end = 0
    for match in _UNTAGGED_TITLE.finditer(text):
        if any(start <= match.start() < end for start, end in spans) or match.start() < previous_end:
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        lines = text[line_start:line_start + 20_000].splitlines()
        block = []
        has_figures = False
        for number_of_line, line in enumerate(lines):
            if number_of_line and (_UNTAGGED_END.search(line) or _UNTAGGED_TITLE.match(line) and has_figures):
                break  # the page's end, or the next statement once this one has figures
            block.append(line)
            parsed = _split(line)
            has_figures = has_figures or bool(parsed and parsed.figures and parsed.label)
            if len(block) > 160:
                break
        first = next((index for index, line in enumerate(block)
                      if (parsed := _split(line)) and parsed.figures and parsed.label
                      and not all(_YEAR.match(chunk.text) for chunk in parsed.figures)), None)
        if first is None:
            continue
        top = block[:first]
        headed = [index for index, line in enumerate(top) if COLUMN_TITLE.search(line) or
                  any(_YEAR.match(chunk.text) for chunk in _chunks(line))]
        split_at = headed[0] if headed else first
        # The text before is this statement's own, never the statement before it: Apple's 1996 balance sheet would
        # otherwise take the "(in thousands)" of the share line ending its income statement as a second scale.
        before = text[max(previous_end, line_start - 1500):line_start]
        previous_end = line_start + sum(len(line) + 1 for line in block)
        yield line_start, before, top[:split_at], top[split_at:], block[first:]


def parse_text(text: str) -> list[Table]:
    """Every table of a plain-text document, tagged <TABLE> blocks and untagged statements, in document order, each with
    the text before it (its title and scale)."""
    # A tab stands for the spaces to the next stop of eight: Apple's 1996-97 10-Qs end some wrapped labels with tabs,
    # which would put the figure after them in no column.
    text = "\n".join(line.expandtabs(8) for line in text.split("\n"))
    spans = [(block.start(), block.end()) for block in _BLOCK.finditer(text)]
    parts = sorted([*_tagged(text), *_untagged(text, spans)], key=lambda part: part[0])
    return [_table(before, title, header, body, index) for index, (_start, before, title, header, body) in enumerate(parts)]
