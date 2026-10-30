"""A quarter's segment and product revenue from its earnings release, no AI, while its report is not filed yet.

Alphabet's release gives the quarter's revenue by segment and by product line in a table days before the 10-Q, whose
XBRL is what the breakdowns are read from. The lines are the ones the company's reports gave last quarter, found in
the release by their names. A table is taken only when it holds every line, its year-ago column gives the figures the
report gave a year ago (so it is the quarter's own column, not a year to date), and its lines add up to the table's
total revenue. The report's own figures replace these once it is filed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .document import Document, Table, clean, parse_number
from .xbrl import member_key

_YEAR = re.compile(r"^\s*((?:19|20)\d{2})\s*$")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
           "december")
_LONGER = re.compile(r"\b(six|nine|twelve)\s+months\b|\byears?\s+ended\b", re.I)
_QUARTER = re.compile(r"\bthree\s+months\b|\bquarters?\s+ended\b", re.I)
_TOTAL = re.compile(r"^(total )?(net )?(revenues?|sales)$", re.I)
_FOOTNOTE = re.compile(r"\s*\(\d{1,2}\)|(?<=[a-z])\d{1,2}$", re.I)


@dataclass
class ReleaseBreakdown:
    table: str
    values: dict[str, float]  # line key → the quarter's amount
    rows: dict[str, str]  # line key → the release's own row label
    prior: dict[str, float] = field(default_factory=dict)  # line key → the year-ago column's amount
    total: float | None = None


def line_key(label: str) -> str:
    """A row label as the series key its report gives it ("Google Services total" is Google Services)."""
    text = _FOOTNOTE.sub("", clean(label))
    text = re.sub(r"\btotal\b", "", text, flags=re.I).strip()
    return member_key(text) if text else ""


def _columns(table: Table, period_end: date) -> tuple[int, int, int] | None:
    """(first body row, the quarter's column, the year-ago column) of a table headed by the quarter's month and years."""
    for index, row in enumerate(table.rows[:4]):
        years = {col: int(_YEAR.match(cell).group(1)) for col, cell in enumerate(row) if col and _YEAR.match(cell or "")}
        if not years:
            continue
        header = f"{table.context[-200:]} {' '.join(' '.join(cells) for cells in table.rows[:index])}".lower()
        if _MONTHS[period_end.month - 1] not in header:
            return None
        if _LONGER.search(header) and not _QUARTER.search(header):
            return None  # a year to date only
        current = [col for col, year in years.items() if year == period_end.year]
        prior = [col for col, year in years.items() if year == period_end.year - 1]
        if not current or not prior:
            return None
        return index + 1, current[0], prior[0]
    return None


def _amount(row: list[str], col: int, scale: float) -> float | None:
    parsed = parse_number(row[col] if col < len(row) else None)
    return None if parsed is None or parsed.percent else round(parsed.value * scale, 2)


# A segment table laid out by heading (Amazon's "North America" over its "Net sales" and "Operating income" rows): the
# row of the measure under each heading is that segment's, and under "Consolidated" (or "Total") the company's.
_MEASURES = {
    "revenue": re.compile(r"^(total )?(net )?(sales|revenues?)$", re.I),
    "income": re.compile(r"^(operating income( \(loss\))?|operating (loss|profit)|income( \(loss\))? from operations)$", re.I),
}
_WHOLE = re.compile(r"^(consolidated|total)$", re.I)


def _rows(table: Table, start: int, current: int, measure: str) -> list[tuple[str, list[str], bool]]:
    """(label, row, is the total) for each figure row: a measure row under a heading takes the heading's name."""
    rows, heading = [], None
    for row in table.rows[start:]:
        label = clean(row[0]) if row else ""
        if not label:
            continue
        figures = any(parse_number(cell) is not None for cell in row[1:])
        if not figures:
            heading = label.rstrip(":")
            continue
        if heading and _MEASURES[measure].match(label):
            rows.append((heading, row, bool(_WHOLE.match(heading))))
        elif measure == "revenue":
            rows.append((label, row, bool(_TOTAL.match(label))))
    return rows


_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", re.I)
_WHOLE_COLUMN = re.compile(r"^(total )?consolidated$|^total$", re.I)
_LEFT_OVER = "unallocated"
_FIGURE = re.compile(r"^[$€£¥(]*\s*[\d.,]+\)?%?$|^[—–-]$")


def _table_date(table: Table) -> date | None:
    for row in table.rows[:4]:
        for cell in row:
            if match := _DATE.search(cell or ""):
                try:
                    return date(int(match.group(3)), _MONTHS.index(next(m for m in _MONTHS if m.startswith(match.group(1).lower()))) + 1,
                                int(match.group(2)))
                except ValueError:
                    return None
    return None


def _columned(table: Table, measure: str) -> tuple[list[str], list[str]] | None:
    """(column names, the measure's row) of a table laid out with its segments as columns (Intel's supplemental
    segment results: "CCPG", "DCAI", … across, "Revenue" and "Operating income (loss)" down)."""
    names = None
    for index, row in enumerate(table.rows[:6]):
        texts = [clean(cell) for cell in row[1:]]
        if sum(1 for text in texts if re.search(r"[a-z]{2}", text, re.I)) >= 3 and not any(_FIGURE.match(text) for text in texts):
            names = ["", *texts]
        elif names and _MEASURES[measure].match(clean(row[0]) if row else ""):
            return names, row
    if names:
        for row in table.rows:
            if row and _MEASURES[measure].match(clean(row[0])):
                return names, row
    return None


def _columned_items(document: Document, table: Table, period_end: date, measure: str, scale: float):
    """A columned table of the quarter, with the same table of a year before as its year-ago column."""
    header = f"{table.context[-200:]} {' '.join(' '.join(cells) for cells in table.rows[:3])}".lower()
    if _table_date(table) != period_end or (_LONGER.search(header) and not _QUARTER.search(header)):
        return None
    layout = _columned(table, measure)
    if layout is None:
        return None
    names, row = layout
    prior_row = None
    for other in document.tables.values():
        when = _table_date(other) if other is not table else None
        if when and 357 <= (period_end - when).days <= 371 and (found := _columned(other, measure)) and found[0] == names:
            prior_row = found[1]
            break
    items = []
    for col, name in enumerate(names):
        if col and name:
            current = _amount(row, col, scale)
            if current is not None:
                items.append((name, current, _amount(prior_row, col, scale) if prior_row else None, bool(_WHOLE_COLUMN.match(name))))
    return items


def _items(document: Document, table: Table, period_end: date, measure: str, scale: float):
    """(label, the quarter's amount, the year-ago amount, is the total) for each line of the table, or None."""
    columns = _columns(table, period_end)
    if columns is None:
        return _columned_items(document, table, period_end, measure, scale)
    start, current, prior = columns
    return [(label, value, _amount(row, prior, scale), whole) for label, row, whole in _rows(table, start, current, measure)
            if (value := _amount(row, current, scale)) is not None]


def release_breakdown(document: Document, members: dict[str, str], year_ago: dict[str, float], period_end: date, *,
                      tables: list[str] | None = None, totalled: bool = True, measure: str = "revenue") -> ReleaseBreakdown | None:
    """The quarter's figure for every line in `members` (key → label) from one table of the release.

    `year_ago` holds the report's figures for the same quarter a year before; every one of them must be the table's
    year-ago figure (to its rounding), and at least two must be compared unless the table was already proven by a
    breakdown read from it (`tables`). With `totalled`, the lines must add up to the table's total revenue. An
    "Unallocated" line of operating income, which the report works out, is what the table's segments leave of its
    total."""
    wanted = {key: {key, line_key(label)} for key, label in members.items()}
    for table_id in tables or list(document.tables):
        table = document.tables.get(table_id)
        if table is None:
            continue
        scale = table.declared_scale() or document.default_scale()
        items = _items(document, table, period_end, measure, scale) if scale else None
        if not items:
            continue
        found: dict[str, tuple[str, float, float | None]] = {}
        total: float | None = None
        prior_total: float | None = None
        for label, value, prior_value, whole in items:
            if whole:
                if total is None:
                    total, prior_total = value, prior_value
                continue
            key = line_key(label)
            for member, names in wanted.items():
                if member not in found and key in names:
                    found[member] = (label, value, prior_value)
        left = [member for member in members if member not in found]
        if measure == "income" and total is not None and len(left) == 1 and _LEFT_OVER in wanted[left[0]]:
            rest = [found[member] for member in members if member in found]
            prior = None if prior_total is None or any(item[2] is None for item in rest) else \
                round(prior_total - sum(item[2] for item in rest), 2)
            found[left[0]] = ("Unallocated", round(total - sum(item[1] for item in rest), 2), prior)
        if set(found) != set(members):
            continue
        tolerance = scale * 1.5
        compared = [member for member in members if member in year_ago]
        if len(compared) < (0 if tables else 2) or any(found[member][2] is None or abs(found[member][2] - year_ago[member]) > tolerance
                                    for member in compared):
            continue
        values = {member: found[member][1] for member in members}
        if totalled and (total is None or abs(sum(values.values()) - total) > tolerance * len(values)):
            continue
        return ReleaseBreakdown(table.id, values, {member: found[member][0] for member in members},
                                {member: found[member][2] for member in members if found[member][2] is not None}, total)
    return None
