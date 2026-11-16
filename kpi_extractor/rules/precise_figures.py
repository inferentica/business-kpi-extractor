"""A figure a filing prints rounded in one place and in full in another is read in full. BNY's supplement gives its
assets under management as "$2.2" trillion on its highlights page and "$2,187" billion in its AUM table; Truist's
release gives average deposits as "405" billion in its summary and "$404,869" million in its average balances. A reader
that took the rounded figure read the filing right, to fewer digits: which table it chose varied with each fresh answer.
The full figure is taken when it is the one figure in the filing that rounds to the reading, on a row naming the same
metric, in a column naming the same quarter, averaged as the reading is (or not), on no segment's page. A reading that is
no average never takes a figure anything around it calls an average (a heading in any case: "Average Balance Sheet Data"),
and a label's own qualifier ("Deposits (excluding brokered)") names another measure."""
from __future__ import annotations

import re
from datetime import date

from ..documents.document import Document, Table, parse_number
from ..quality.checks import PERIOD_TOLERANCE_DAYS
from ..readers.period_column import _OTHER_MEASURE, _spans, comparable_header
from .numbers import same_figure
from .period import DEEP_HEADER_ROWS, column_dates, column_header
from .scale import _row_scale_words, currency_alone, document_scale_words, table_scale_words
from .segment_tables import _heading_line, _label_key, segment_of
from .table import _heading

# Words that say what kind of total a row holds, not what it is: "Total deposits" and "Deposits" are one metric.
_PLAIN = {"total", "memo", "the", "of", "at", "and", "balance", "balances"}
# An average balance and a period-end balance of one metric are two figures; a beginning balance is last quarter's end.
_AVERAGE = {"average", "averages", "avg"}
_MEASURES = _AVERAGE | {"beginning", "ending", "end", "period"}
_DIGITS = re.compile(r"\d[\d,]*(?:\.(\d+))?|\.(\d+)")
_QUARTER_TAG = re.compile(r"\b([1-4])q\s?['’]?(\d{2}(?:\d{2})?)\b|\bq([1-4])\s?['’]?\s?(\d{2}(?:\d{2})?)\b")
_QUARTER_WORD = re.compile(r"\b(first|second|third|fourth)\s*quarter\b")
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_YEAR_CELL = re.compile(r"\s*(?:19|20)\d{2}\s*")
_OTHER_SPAN = re.compile(r"\bnine months\b|\bsix months\b|\byear[- ]to[- ]date\b|\bytd|\bfull[- ]year\b|\bvs\b|\bversus\b|"
                         r"\bchange\b|%")


def printed_step(text: str) -> float | None:
    """The last place a printed figure states, in its own units: "2.2" to 0.1, "$404,869" to 1."""
    match = _DIGITS.search(text or "")
    if not match:
        return None
    decimals = match.group(1) or match.group(2) or ""
    return 10.0 ** -len(decimals)


def _printed(text: str) -> float | None:
    """The printed digits' own value, before any scale: "$2.2 trillion" is 2.2."""
    match = _DIGITS.search(text or "")
    return float(match.group(0).replace(",", "")) if match else None


def _title(table: Table) -> str:
    """A table's title: the heading line above it and its label column above the first figure ("Average deposits" over
    a quarter's column), never a neighbouring column's header ("Average balances" beside "Period-end balances")."""
    return " ".join([_heading_line(table.context), *(row[0] for row in table.rows[:first_data_row(table)] if row)])


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower().replace("&", " and ").replace("-", " "))


# What a label's parentheses may hold without naming another measure: a unit or scale, a footnote mark, a note on the
# figure's status. Anything else ("excluding brokered", "net of reserves") qualifies the metric.
_PLAIN_NOTE = re.compile(r"^\s*(?:[a-z]|[ivx]{1,4}|\d{1,2}|#)\s*$|\b(?:in\s+)?(?:thousands|millions|billions|trillions)\b|"
                         r"^\s*(?:\$|us\$|u\.s\.\s*dollars|dollars|%|percent|bps|basis points)\s*$|"
                         r"preliminary|unaudited|estimated|annualized|see note", re.I)


def _qualifiers(label: str) -> tuple[str, ...]:
    """The words of a label's parentheses that qualify its metric, without units, footnote marks, status notes and the
    label's own initials ("Assets under management (“AUM”)")."""
    outside = _words(re.sub(r"\([^)]*\)", " ", label or ""))
    initials = "".join(word[0] for word in outside if word not in _PLAIN)
    words: list[str] = []
    for inner in re.findall(r"\(([^)]*)\)", label or ""):
        letters = "".join(_words(inner))
        if _PLAIN_NOTE.search(inner) or not letters or (letters.isalpha() and len(letters) <= 6
                                                         and (letters == initials or letters in outside)):
            continue
        words.extend(_words(inner))
    return tuple(words)


def _core(label: str) -> tuple[str, ...]:
    """A row label's own words, without notes, totals' words or measures: "Memo: Total deposits" is "deposits"; a
    qualifier in parentheses stays ("Deposits (excluding brokered)" is "deposits excluding brokered")."""
    return tuple(word for word in _label_key(label).split() if word not in _PLAIN and word not in _MEASURES) + _qualifiers(label)


def _same_metric(label: str, names: list[str]) -> bool:
    """Whether a row label names the metric one of `names` does, word for word or by its initials ("Total AUM" and
    "Assets under management")."""
    core = _core(label)
    if not core:
        return False
    for name in names:
        other = _core(name)
        if not other:
            continue
        if core == other or (len(core) == 1 and len(other) > 1 and core[0] == "".join(word[0] for word in other)) \
                or (len(other) == 1 and len(core) > 1 and other[0] == "".join(word[0] for word in core)):
            return True
    return False


def _averaged(*texts: str) -> bool:
    return any(word in _AVERAGE for text in texts for word in _words(text))


def _heading_any_case(context: str) -> str:
    """The short line printed just above a table, in any case ("Average Balance Sheet Data (Dollars in millions)")."""
    tail = re.split(r"[.;:!?]\s", (context or "")[-200:])[-1].strip()
    return tail if len(tail) <= 120 else ""


def _called_average(table: Table, label: str, header: str) -> bool:
    """Whether anything around a cell calls it an average: its label, its column's header, its table's title rows or the
    text just above the table, in any case. A reading that is no average never takes such a cell."""
    return _averaged(label, header, _title(table), (table.context or "")[-200:])


def _stated_average(table: Table, label: str, header: str) -> bool:
    """Whether the cell's own label, header, title rows or heading line say it is an average: what an average's full
    figure must show."""
    return _averaged(label, header, _title(table), _heading_any_case(table.context))


def first_data_row(table: Table) -> int:
    """The first row with a label and a figure: a header's "3Q25" or "2026" is no figure."""
    for index, row in enumerate(table.rows):
        if row and row[0].strip() and any(parse_number(cell) is not None and not re.search(r"[A-Za-z]", cell)
                                          and not _YEAR_CELL.fullmatch(cell) for cell in row[1:]):
            return index
    return len(table.rows)


def _header_text(table: Table, col: int) -> str:
    """The words over a column, from the rows above the table's first figure."""
    return " ".join(cell for line in table.rows[:first_data_row(table)][:DEEP_HEADER_ROWS] if col < len(line)
                    and (cell := _heading(line, col))).lower()


def names_quarter(table: Table, col: int, expected: date, quarter: int | None, fiscal_year: int | None = None) -> bool:
    """Whether a column's own header names the quarter ending `expected`, and no other period: its dates within the
    period tolerance of its end, or its quarter's name ("3Q26", "Q3 2026", "third quarter" over "2026") with this year
    or the next (a fiscal year named for its end), or exactly `fiscal_year` when it is known; never a change ("3Q25 vs. 2Q25"), a half, nine months, a year or a
    year to date ("YTD25")."""
    text = _header_text(table, col)
    first = first_data_row(table)
    if not text or _spans(comparable_header(column_header(table, first, col, DEEP_HEADER_ROWS))) - {"quarter"} \
            or _OTHER_SPAN.search(text):
        return False
    years = {fiscal_year} if fiscal_year else {expected.year, expected.year + 1}
    dates, named_years = column_dates(table, first, col)
    tags = [(int(a or c), int(b or d) % 100 + 2000) for a, b, c, d in _QUARTER_TAG.findall(text)]
    tags += [(_ORDINALS[word], year) for word in _QUARTER_WORD.findall(text) for year in named_years]
    if not dates and not tags:
        return False
    return all(abs((day - expected).days) <= PERIOD_TOLERANCE_DAYS for day in dates) \
        and all(quarter is not None and named == quarter and year in years for named, year in tags)


def _cell_scale(document: Document, table: Table, row: int, number) -> float | None:
    if number.scale_word is not None:
        return 1.0
    if (alone := currency_alone(table, table.rows[row][0] if table.rows[row] else "")) is not None:
        return alone[0] if alone else None  # a row naming its currency alone, in units only where the caption says so
    found = _row_scale_words(table.rows[row][0] if table.rows[row] else "") or table_scale_words(table) \
        or document_scale_words(document)
    return found[0] if found else None


def precise_cell(document: Document, reading: dict, value: float, kpi_label: str, expected: date, quarter: int | None,
                 segments: list[str], fiscal_year: int | None = None) -> tuple[str, int, int, str, float] | None:
    """The one cell that prints a reading's rounded figure in full: (table, row, column, row label, scale), or None.
    `reading` is the reading's locator (a table cell, or a quoted figure). Every cell in the filing that rounds to the
    reading on a row naming its metric, in a column naming its quarter, averaged as the reading is, must give one figure."""
    table_id, row, col = reading.get("table"), reading.get("row"), reading.get("col")
    own = document.tables.get(str(table_id)) if table_id is not None else None
    if own is not None and row is not None and col is not None:
        printed = own.cell(int(row), int(col))
        names = [str(reading.get("row_label") or ""), kpi_label]
        averaged = _averaged(kpi_label) or _stated_average(own, str(reading.get("row_label") or ""), _header_text(own, int(col)))
        own_segment = segment_of(own, segments)
    else:
        printed = str(reading.get("value_text") or "")
        names = [kpi_label]
        averaged = _averaged(kpi_label, str(reading.get("quote") or ""))
        own_segment = None
    number = parse_number(printed)
    step = printed_step(printed)
    if number is None or not number.value or step is None or not value:
        return None
    raw = _printed(printed)
    if not raw:
        return None
    unit = abs(value / raw)
    half = step * unit / 2
    if half <= 0:
        return None
    found: list[tuple[str, int, int, str, float]] = []
    figures: list[float] = []
    for table in document.tables.values():
        if segment_of(table, segments) not in (None, own_segment):
            continue
        for index, cells in enumerate(table.rows):
            if not cells or not _same_metric(cells[0], names):
                continue
            if "beginning" in _words(cells[0]):
                continue
            for column in range(1, len(cells)):
                if table is own and index == int(row) and column == int(col):
                    continue
                cell = parse_number(table.cell(index, column))
                cell_step, cell_raw = printed_step(table.cell(index, column)), _printed(table.cell(index, column))
                if cell is None or not cell.value or cell.percent or cell_step is None or not cell_raw:
                    continue
                scale = _cell_scale(document, table, index, cell)
                if scale is None:
                    continue
                full = cell.value * scale
                if cell_step * abs(full / cell_raw) >= step * unit or abs(full - value) > half * (1 + 1e-9):
                    continue
                header = _header_text(table, column)
                if averaged and not _stated_average(table, cells[0], header) \
                        or not averaged and _called_average(table, cells[0], header) \
                        or not names_quarter(table, column, expected, quarter, fiscal_year):
                    continue
                header_words = set(re.findall(r"[a-z][a-z-]*[a-z]", header))
                if any(_OTHER_MEASURE.match(word) for word in header_words):
                    continue
                found.append((table.id, index, column, cells[0], scale))
                figures.append(full)
    # The same figure in another cell is no fuller (ASML's units, printed in two tables): the reading stands.
    if not found or any(not same_figure(figure, figures[0]) for figure in figures) or same_figure(figures[0], value):
        return None
    return found[0]
