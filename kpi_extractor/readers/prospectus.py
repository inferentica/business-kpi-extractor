"""A recent listing's history from its IPO prospectus, no AI: the revenue breakdowns its reports tag, for every period
the prospectus's own tables give (SpaceX: full years 2023 to 2025, the first quarters of 2025 and 2026).

A table is read only when it holds every line of a breakdown and its total revenue, and the lines add up to the total in
every period column. One line the company renamed after listing (SpaceX's AI segment, SI by its second 10-Q) is the
one other row of the table whose figure is the total less the lines found, in every column."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from ..documents.document import Document, Table, clean, parse_number
from .release_breakdowns import line_key

_MONTHS = {name: index + 1 for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
_SPAN = re.compile(r"\b(?:(?P<quarter>three months|quarters?)|(?P<year>years?|fiscal years?|twelve months))\s+ended\s+"
                   r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})", re.I)
_YEAR = re.compile(r"^\s*((?:19|20)\d{2})\s*$")
_LEADER = re.compile(r"\s*\.{3,}.*$")
_TOTAL = re.compile(r"^(total )?(net )?(revenues?|sales)$", re.I)
SCALES = (1.0, 1e3, 1e6, 1e9)


@dataclass(frozen=True)
class Period:
    end: date
    annual: bool


@dataclass
class Breakdown:
    table: str
    values: dict[str, float]
    total: float
    rows: dict[str, str] = field(default_factory=dict)  # line key → the prospectus's row label


def period_columns(table: Table) -> dict[int, Period]:
    """The period each figure column covers: a header row names the span ("Three Months Ended March 31,", "Year Ended
    December 31,"), carried right across the columns it spans, and the row under it gives each column's year."""
    for index, row in enumerate(table.rows[:5]):
        years = {col: int(_YEAR.match(cell).group(1)) for col, cell in enumerate(row) if col and _YEAR.match(cell or "")}
        if not years:
            continue
        spans: dict[int, tuple[bool, int, int]] = {}
        for header in table.rows[:index]:
            current = None
            for col, cell in enumerate(header):
                match = _SPAN.search(cell or "")
                if match and match.group("month").lower() in _MONTHS:
                    current = (match.group("year") is not None, _MONTHS[match.group("month").lower()], int(match.group("day")))
                if current and col:
                    spans[col] = current
        if not spans:
            match = _SPAN.search(table.context[-200:])
            if match and match.group("month").lower() in _MONTHS:
                spans = {col: (match.group("year") is not None, _MONTHS[match.group("month").lower()], int(match.group("day")))
                         for col in years}
        columns = {}
        for col, year in years.items():
            span = spans.get(col)
            if span is None:
                continue
            try:
                columns[col] = Period(date(year, span[1], span[2]), span[0])
            except ValueError:
                continue
        return columns
    return {}


def _label(cell: str) -> str:
    return _LEADER.sub("", clean(cell or ""))


def _amount(row: list[str], col: int) -> float | None:
    parsed = parse_number(row[col] if col < len(row) else None)
    return None if parsed is None or parsed.percent else parsed.value


def scale_for(printed: float, reported: float, annual: bool) -> float | None:
    """The scale that puts a prospectus total next to a reported quarter's (a year about four quarters)."""
    if printed <= 0 or reported <= 0:
        return None
    target = reported * (4 if annual else 1)
    best = min(SCALES, key=lambda scale: abs(math.log10(printed * scale / target)))
    return best if abs(math.log10(printed * best / target)) < 1 else None


def prospectus_breakdowns(document: Document, members: dict[str, str], reported_total: float) -> dict[Period, Breakdown]:
    """Every period a table of the prospectus gives the breakdown `members` (key → label) for, in full amounts."""
    found: dict[Period, Breakdown] = {}
    wanted = {key: {key, line_key(label)} for key, label in members.items()}
    for table in document.tables.values():
        columns = period_columns(table)
        if not columns:
            continue
        rows = [(_label(row[0]), row) for row in table.rows if row and _label(row[0])
                and any(_amount(row, col) is not None for col in columns)]
        total_row = next((row for label, row in rows if _TOTAL.match(label)), None)
        if total_row is None:
            continue
        matched: dict[str, tuple[str, list[str]]] = {}
        for label, row in rows:
            key = line_key(label)
            for member, names in wanted.items():
                if member not in matched and key in names:
                    matched[member] = (label, row)
        unmatched = [member for member in members if member not in matched]
        if len(unmatched) > 1 or len(matched) < 2:
            continue
        sample = next(iter(columns))
        printed_total = _amount(total_row, sample)
        scale = table.declared_scale() or document.default_scale() or (
            scale_for(printed_total or 0, reported_total, columns[sample].annual) if printed_total else None)
        if scale is None:
            continue
        tolerance = 1.5 * len(members)  # each line rounded to the table's unit
        if unmatched:
            # The renamed line: the one other row equal to the total less the lines found, in every column.
            used = {id(row) for _label_, row in matched.values()} | {id(total_row)}
            candidates = [(label, row) for label, row in rows if id(row) not in used and all(
                _amount(row, col) is not None and _amount(total_row, col) is not None
                and abs(_amount(total_row, col) - sum(_amount(r, col) or 0 for _l, r in matched.values()) - _amount(row, col)) <= tolerance
                for col in columns)]
            if len(candidates) != 1:
                continue
            matched[unmatched[0]] = candidates[0]
        for col, period in columns.items():
            if period in found:
                continue
            total = _amount(total_row, col)
            values = {member: _amount(row, col) for member, (_label_, row) in matched.items()}
            if total is None or any(value is None for value in values.values()):
                continue
            if abs(sum(values.values()) - total) > tolerance:
                continue
            found[period] = Breakdown(table.id, {member: value * scale for member, value in values.items()}, total * scale,
                                      {member: label for member, (label, _row) in matched.items()})
    return found


def interim_quarters(document: Document) -> list[date]:
    """The quarters the prospectus's tables give on their own (its latest interim period and the one a year before),
    newest first."""
    ends = {period.end for table in document.tables.values() for period in period_columns(table).values() if not period.annual}
    return sorted(ends, reverse=True)


def quarter_sections(document: Document, labels: list[str], end: date) -> Document:
    """The parts of a prospectus that can give a KPI for one quarter: a table or passage naming a listed KPI (by its
    words) and that quarter ("March 31, 2026"). A prospectus names its KPIs in hundreds of places (risk factors, the
    business), which would crowd its key-metrics section out of the reader's budget."""
    month = [name for name, number in _MONTHS.items() if number == end.month][0].capitalize()
    dated = re.compile(rf"{month}\s+{end.day},?\s+{end.year}|{month}\s+{end.day},?.{{0,80}}\b{end.year}\b", re.S)
    words = [[word for word in re.findall(r"[a-z0-9]+", label.lower()) if len(word) > 2] for label in labels]
    keep = []
    for key in document.order:
        item = document.tables.get(key) or document.blocks[key]
        text = item.text if key in document.blocks else f"{item.context} {' '.join(' '.join(row) for row in item.rows)}"
        lowered = text.lower()
        if dated.search(text) and re.search(r"\d", text) and any(group and all(word in lowered for word in group) for group in words):
            keep.append(key)
    return Document(document.source_url, {key: document.tables[key] for key in keep if key in document.tables},
                    {key: document.blocks[key] for key in keep if key in document.blocks}, keep)


_SEGMENT_HEADING = re.compile(r"([A-Z][A-Za-z0-9&,.'\- ]{0,40}):\s*$")
_SEGMENT_INCOME = re.compile(r"^segment (?:income|loss|income \(loss\)|\(loss\) income) from operations\b", re.I)
_ROW_SCALE = {"millions": 1e6, "thousands": 1e3, "billions": 1e9}


@dataclass
class SegmentIncome:
    values: dict[str, float]  # line key → the segment's income (loss) from operations, in full amounts
    tables: dict[str, str]  # line key → the table it was read from
    names: dict[str, str]  # line key → the prospectus's name for the segment


def prospectus_segment_income(document: Document, members: dict[str, str],
                              renamed: dict[str, str] | None = None) -> dict[Period, SegmentIncome]:
    """Each segment's income (loss) from operations for every period the prospectus's per-segment tables give, when
    every segment of `members` (key → label) is found for it. SpaceX's prospectus gives one small table per segment
    under its name ("Space:", "Connectivity:", "AI:"), each with a "Segment income (loss) from operations" row.
    `renamed`: a prospectus name (line key) → today's key, from the revenue tables; one segment left unnamed is the
    one table left."""
    wanted = {key: {key, line_key(label)} for key, label in members.items()}
    names_to = {name: member for name, member in (renamed or {}).items() if member in members}
    read: dict[str, tuple[str, dict[Period, float]]] = {}  # the prospectus's segment name → (table, figures)
    for table in document.tables.values():
        heading = _SEGMENT_HEADING.search((table.context or "").strip())
        columns = period_columns(table) if heading else {}
        if not columns:
            continue
        row = next((row for row in table.rows if row and _SEGMENT_INCOME.match(_label(row[0]))), None)
        if row is None:
            continue
        unit = next((scale for word, scale in _ROW_SCALE.items() if f"in {word}" in (row[0] or "").lower()), None)
        scale = unit or table.declared_scale() or document.default_scale()
        figures = {period: amount * scale for col, period in columns.items() if (amount := _amount(row, col)) is not None}
        if scale and figures:
            read.setdefault(heading.group(1).strip(), (table.id, figures))
    matched: dict[str, str] = {}  # member → the prospectus's name
    for name in read:
        key = line_key(name)
        member = names_to.get(key) or next((m for m, keys in wanted.items() if key in keys and m not in matched), None)
        if member and member not in matched:
            matched[member] = name
    unmatched = [member for member in members if member not in matched]
    left = [name for name in read if name not in matched.values()]
    if len(unmatched) == 1 and len(left) == 1:
        matched[unmatched[0]] = left[0]
    if set(matched) != set(members):
        return {}
    periods = set.intersection(*(set(read[name][1]) for name in matched.values()))
    return {period: SegmentIncome({member: read[name][1][period] for member, name in matched.items()},
                                  {member: read[name][0] for member, name in matched.items()}, dict(matched))
            for period in periods}
