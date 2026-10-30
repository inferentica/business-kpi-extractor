"""The earlier quarters an earnings release's tables show beside its own: the prior quarter, the year-ago quarter, and
a six-month column less its quarter (SpaceX's Q2 2026 release: Q1 2026, Q2 2025, and Q1 2025 as the half year less
Q2). A recent listing has no releases of its own for those quarters.

A release gives these tables as HTML tables (SpaceX's Q3 2026) or as running text (its Q2 2026: "Three Months Ended
Six Months Ended June 30, 2026 March 31, 2026 June 30, 2025 June 30, 2026 June 30, 2025 Customer launches (#) 10 7 9 17
21 …"); both become grids of rows under dated columns."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from .document import Document, Table, clean, parse_number
from .release_breakdowns import line_key

_MONTHS = {name: index + 1 for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*((?:19|20)\d{2})")
_SPAN = re.compile(r"\b(three|six|nine|twelve)\s+months\s+ended\b", re.I)
# The spans heading a text table, straight before its first date (prose later in the block may name a span too).
_HEADER = re.compile(r"(?:\b(?:three|six|nine|twelve)\s+months\s+ended\s+)+(?=(?:January|February|March|April|May|June|July|"
                     r"August|September|October|November|December)\s+\d)", re.I)
_NUMBER = re.compile(r"^\$?\(?-?\$?[\d,]*\.?\d+\)?%?$|^[—–-]$")
_REVENUE = re.compile(r"(?<=.)(revenues?|sales)$")


@dataclass(frozen=True)
class Column:
    end: date
    months: int  # 3 for a quarter, 6 or 9 for a year to date


@dataclass
class Grid:
    source: str  # the table or text block it came from
    columns: list[Column]
    rows: list[tuple[str, list[str]]] = field(default_factory=list)


def _date(match: re.Match) -> date | None:
    try:
        return date(int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)))
    except ValueError:
        return None


def _months(word: str) -> int:
    return {"three": 3, "six": 6, "nine": 9, "twelve": 12}[word.lower()]


def _table_grid(table: Table) -> Grid | None:
    """A table headed by a row of spans ("Three Months Ended", "Nine Months Ended") over a row of full dates."""
    for index, row in enumerate(table.rows[:4]):
        dates = {col: _DATE.search(cell or "") for col, cell in enumerate(row) if col}
        dates = {col: found for col, found in dates.items() if found}
        if len(dates) < 2:
            continue
        spans: dict[int, int] = {}
        for header in table.rows[:index]:
            current = None
            for col, cell in enumerate(header):
                match = _SPAN.search(cell or "")
                if match:
                    current = _months(match.group(1))
                if current and col:
                    spans[col] = current
        columns, cols = [], []
        for col, found in sorted(dates.items()):
            end = _date(found)
            if end is None or col not in spans:
                return None
            columns.append(Column(end, spans[col]))
            cols.append(col)
        grid = Grid(table.id, columns)
        for body in table.rows[index + 1:]:
            label = clean(body[0] if body else "")
            if label:
                grid.rows.append((label, [clean(body[col]) if col < len(body) else "" for col in cols]))
        return grid
    return None


def _text_grid(key: str, text: str) -> Grid | None:
    """Running text: its spans, then as many dates as columns, then rows of a label and one figure a column. The
    year-to-date columns start where a date repeats the first one."""
    header = _HEADER.search(text)
    if not header:
        return None
    spans = list(_SPAN.finditer(header.group(0)))
    dates = []
    position = header.end()
    for match in _DATE.finditer(text, position):
        if text[position:match.start()].strip():
            break
        dates.append(_date(match))
        position = match.end()
    if len(dates) < 2 or None in dates:
        return None
    restart = next((index for index in range(1, len(dates)) if dates[index] == dates[0]), len(dates))
    months = [_months(span.group(1)) for span in spans]
    columns = [Column(end, months[0] if index < restart or len(months) == 1 else months[1]) for index, end in enumerate(dates)]
    grid = Grid(key, columns)
    tokens = text[position:].split()
    label: list[str] = []
    index = 0
    while index < len(tokens):
        run = tokens[index:index + len(columns)]
        if label and len(run) == len(columns) and all(_NUMBER.match(token) for token in run):
            grid.rows.append((" ".join(label), run))
            label, index = [], index + len(columns)
            continue
        label.append(tokens[index])
        index += 1
        if len(label) > 25:
            label = label[-25:]  # prose between tables: only the words just before a row name it
    return grid


def release_grids(document: Document) -> list[Grid]:
    grids = []
    for key in document.order:
        if key in document.tables:
            grid = _table_grid(document.tables[key])
        else:
            grid = _text_grid(key, document.blocks[key].text)
        if grid and grid.rows:
            grids.append(grid)
    return grids


def amount(cell: str) -> float | None:
    parsed = parse_number(cell)
    return None if parsed is None or parsed.percent else parsed.value


def row_key(label: str) -> str:
    """A row as the series key its report gives it: "Launch services revenues" is Launch Services."""
    key = line_key(re.sub(r"\s*\([^)]*\)\s*$", "", label))
    return _REVENUE.sub("", key) or key


def earlier_quarters(grid: Grid, cells: list[str], own_end: date, additive: bool) -> dict[date, float]:
    """A row's quarters other than the release's own: its three-month columns, and, for an amount that adds up over
    time (revenue, not subscribers or a price per month), a six-month column less the three-month column ending the
    same day (the quarter before it). Figures as printed."""
    found: dict[date, float] = {}
    quarters = {column.end: amount(cell) for column, cell in zip(grid.columns, cells) if column.months == 3}
    for end, value in quarters.items():
        if end != own_end and value is not None:
            found[end] = value
    for column, cell in zip(grid.columns, cells):
        half, quarter = amount(cell), quarters.get(column.end)
        if additive and column.months == 6 and half is not None and quarter is not None:
            month = (column.end.month - 4) % 12 + 1
            year = column.end.year - (1 if month > column.end.month else 0)
            found.setdefault(date(year, month, calendar.monthrange(year, month)[1]), half - quarter)
    return found


# A table headed by quarter names rather than dates (ASML's slides: "Q2 2022 Q3 2022 Q4 2022 Q1 2023 Q2 2023 Net sales
# 5,431 5,778 …"), as its slide text runs.
_QUARTER_RUN = re.compile(r"(?:\bQ[1-4]\s+(?:19|20)\d{2}\s+){1,7}Q[1-4]\s+(?:19|20)\d{2}\b")
_QUARTER = re.compile(r"\bQ([1-4])\s+((?:19|20)\d{2})\b")
_FIGURES = re.compile(r"(?:\s+(?:\(?-?[\d,]*\.?\d+\)?|[—–-]))+")


def _label_pattern(label: str) -> re.Pattern:
    label = clean(label)
    # A row starts with its label's own capital; the end of a longer label does not ("New lithography systems sold" for
    # "Lithography systems sold"). A row after another's figures ("… -16.2 ppts Shipment (Kpcs, …)") still matches.
    rest = r"\s+".join(re.escape(word) for word in label[1:].split(" "))
    return re.compile(r"(?<![A-Za-z])" + re.escape(label[:1]) + "(?i:" + rest + ")")


def quarter_table_rows(text: str, label: str) -> list[dict[tuple[int, int], float]]:
    """A row's possible figures under each run of quarter headings in a text: {(year, quarter): figure}, read from its
    first number and from its second (a footnote mark: "Lithography systems sold (units) 1 91 86 106 100 113")."""
    found = []
    pattern = _label_pattern(label)
    runs = list(_QUARTER_RUN.finditer(text))
    for index, run in enumerate(runs):
        quarters = [(int(year), int(quarter)) for quarter, year in _QUARTER.findall(run.group(0))]
        if len(set(quarters)) != len(quarters):
            continue
        section = text[run.end():runs[index + 1].start() if index + 1 < len(runs) else len(text)]
        match = pattern.search(section)
        figures = _FIGURES.match(section, match.end()) if match else None
        if not figures:
            continue
        cells = figures.group(0).split()
        # The figures start at the first number or after a footnote mark, and a footnote mark may follow them ("Sale of
        # lithography systems (in units) 1 72 102 79 91 121 1"): each reading is offered; the caller keeps the one that
        # agrees with figures it holds.
        for offset in (0, 1):
            if offset + len(quarters) <= len(cells) <= len(quarters) + 2:
                row = {quarter: value for quarter, cell in zip(quarters, cells[offset:]) if (value := amount(cell)) is not None}
                if row:
                    found.append(row)
    return found


_QUOTES = str.maketrans({"“": '"', "”": '"', "″": '"', "‘": "'", "’": "'"})
_ROW_NUMBER = re.compile(r"\(?-?[\d,]*\.?\d+\)?%?")


def row_numbers(text: str, label: str) -> list[list[float]]:
    """The figures straight after each mention of a row label, percentages left out ("Shipment (Kpcs, 12"-equiv. Wafer)
    2,916 3,227 3,799 -9.6% -23.2%" is [2916, 3227, 3799]); figures run together ("2,6992,744") give nothing."""
    text, label = text.translate(_QUOTES), label.translate(_QUOTES)
    found = []
    for match in _label_pattern(label).finditer(text):
        figures = _FIGURES.match(text, match.end())
        if not figures:
            continue
        cells = figures.group(0).split()
        numbers = []
        for cell in cells:
            if cell.endswith("%") or cell.endswith("%)"):
                break
            if re.search(r"\d,\d{3}\d", cell):
                numbers = []
                break
            value = amount(cell)
            if value is None:
                break
            numbers.append(value)
        if numbers:
            found.append(numbers)
    return found
