"""Code readers driven by a line's stored source: one XBRL fact, or one table cell. No AI, no guessing: a source that
resolves to no figure, or to two different figures, says so and the refresh decides what comes of it."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from ..documents.document import Document, Table, clean, parse_number
from ..xbrl.labels import _axis_name, local_name
from .sources import TableSource, XbrlSource

ONE, NONE, MANY = "one", "none", "many"


@dataclass
class Found:
    """What a source gave for one period: one figure, none, or several that differ."""
    status: str
    value: float | None = None
    reason: str = ""
    locator: dict = field(default_factory=dict)
    # A finding about the whole document rather than this line (the table is gone, its scale changed): a basis change.
    basis: bool = False
    # The different figures that matched, in the filing's order (status MANY): a held row shows the first.
    candidates: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Fact:
    concept: str
    start: date | None
    end: date
    dims: frozenset  # (axis, member) local names
    value: float
    precision: float = 1.0  # what the figure is rounded to (decimals -6: a million)


def facts_of(frame) -> list[Fact]:
    """A filing's XBRL facts (edgartools' frame: concept, numeric_value, period_start, period_end, dim_* columns) as plain
    facts, once per filing."""
    if frame is None or len(frame) == 0:
        return []
    columns = [column for column in frame.columns if column.startswith("dim_")]
    facts = []
    for row in frame.to_dict("records"):
        value = row.get("numeric_value")
        end = _day(row.get("period_end") or row.get("period_instant"))
        if value is None or end is None or (isinstance(value, float) and math.isnan(value)):
            continue
        dims = frozenset((_axis_name(column), local_name(str(row[column]))) for column in columns
                         if row.get(column) is not None and not (isinstance(row[column], float) and math.isnan(row[column])))
        facts.append(Fact(local_name(str(row["concept"])), _day(row.get("period_start")), end, dims, float(value),
                          _precision(row.get("decimals"))))
    return facts


def _precision(decimals) -> float:
    try:
        places = int(float(decimals))
    except (TypeError, ValueError, OverflowError):  # "INF": exact
        return 1.0
    return float(10 ** -places) if places < 0 else 1.0


def _day(text) -> date | None:
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


# Days a duration covers: a quarter (13 or 14 weeks), a year to date past the first quarter, a fiscal year (52 or 53 weeks).
_SPANS = {"quarter": (75, 105), "ytd": (150, 300), "fy": (350, 380)}


def _covers(fact: Fact, end: date, duration: str, instant: bool) -> bool:
    if fact.end != end:
        return False
    if instant:
        return fact.start is None or fact.start == fact.end
    if fact.start is None:
        return False
    low, high = _SPANS[duration]
    return low <= (fact.end - fact.start).days <= high


def _members(fact: Fact, ignore: set[str]) -> dict[str, str]:
    return {axis: member for axis, member in fact.dims if member not in ignore and axis not in ignore}


def resolve_xbrl(facts: list[Fact], source: XbrlSource, end: date, duration: str) -> Found:
    concepts, ignore = set(source.concepts), set(source.ignore)
    matched = [fact for fact in facts if fact.concept in concepts and _covers(fact, end, duration, source.instant)
               and _members(fact, ignore) == source.members]
    values = sorted({round(fact.value, 2) for fact in matched})
    ordered = list(dict.fromkeys(round(source.sign * fact.value, 2) for fact in matched))
    locator = {"concepts": source.concepts, "members": source.members, "duration": "instant" if source.instant else duration}
    if not values:
        return Found(NONE, reason=f"no {'/'.join(source.concepts)} fact for {_member_text(source.members)} "
                                  f"({locator['duration']} to {end.isoformat()})", locator=locator)
    if len(values) > 1:
        return Found(MANY, reason=f"{len(values)} different {'/'.join(source.concepts)} facts for "
                                  f"{_member_text(source.members)}: {', '.join(f'{v:,.0f}' for v in values[:4])}", locator=locator,
                     candidates=ordered)
    fact = next(fact for fact in matched if round(fact.value, 2) == values[0])
    return Found(ONE, source.sign * values[0], locator={**locator, "concept": fact.concept, "precision": fact.precision,
                                                         "start": fact.start.isoformat() if fact.start else None})


def _member_text(members: dict[str, str]) -> str:
    return " + ".join(members.values()) if members else "no dimension"


def new_members(facts: list[Fact], sources: list[XbrlSource], known: list[str], end: date, duration: str) -> list[str]:
    """Member sets the filing tags on the lines' own concepts and axes that no line maps: a new segment or product line,
    which is a basis change for the owner's hand pass, never a line built by itself."""
    if not sources:
        return []
    concepts = {concept for source in sources for concept in source.concepts}
    ignore = {member for source in sources for member in source.ignore} | set(known)
    shapes = {frozenset(source.members) for source in sources if source.members}
    mapped = {frozenset(source.members.items()) for source in sources}
    # What every line shares (Dell's ISG lines all carry the ISG member): another table's lines (CSG's) are not new here.
    fixed = frozenset.intersection(*[frozenset(source.members.items()) for source in sources])
    found = set()
    for fact in facts:
        if fact.concept not in concepts or not _covers(fact, end, duration, sources[0].instant):
            continue
        members = _members(fact, ignore)
        if frozenset(members) in shapes and frozenset(members.items()) not in mapped and fixed <= frozenset(members.items()) \
                and not any(member in ignore for member in members.values()):
            found.add(" + ".join(sorted(members.values())))
    return sorted(found)


# --- tables -------------------------------------------------------------------------------------------------------

_FOOTNOTE = re.compile(r"\s*(\((?:[a-z]{1,2}|\d{1,2})\)|\*+|†|‡)(?=\s|$|:)", re.I)
_DASH = re.compile(r"^\(?\s*[-–—]+\s*\)?$")
_MONTHS = {name: index + 1 for index, name in enumerate(
    "january february march april may june july august september october november december".split())}
_DATE = re.compile(r"\b(?P<month>[A-Za-z]{3,9})\.? (?P<day>\d{1,2}),? (?P<year>(?:19|20)\d{2})\b"
                   r"|\b(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>(?:19|20)?\d{2})\b")
_YEAR_ONLY = re.compile(r"\s*(19|20)\d{2}\s*")
_YTD = re.compile(r"\b(six|nine|26|39|twenty-six|thirty-nine)[- ](months|weeks)\b|year[- ]to[- ]date|\bytd\b", re.I)
_QUARTER = re.compile(r"\b(three|13|thirteen|fourteen|14)[- ](months|weeks)\b|\bquarter\b|\bq[1-4]\b", re.I)
_YEAR = re.compile(r"\b(twelve|12|52|53|fifty-two|fifty-three)[- ](months|weeks)\b|\b(fiscal )?years? ended\b|\bfull[- ]year\b|\bfy\b", re.I)


def row_label(text: str) -> str:
    return _FOOTNOTE.sub("", clean(text or "")).rstrip(":").strip()


def _amount(text: str | None) -> float | None:
    if not text or not text.strip():
        return None
    if _DASH.match(text.strip().lstrip("$").strip()):
        return 0.0
    parsed = parse_number(text)
    return None if parsed is None or parsed.percent else parsed.value


def _dates(text: str) -> list[date]:
    found = []
    # Cells whose words run together ("Three Months EndedSeptember 30"): a capital after a small letter starts a word.
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "")
    for match in _DATE.finditer(text):
        try:
            if match.group("month"):
                month = _MONTHS.get(match.group("month").lower()) or next(
                    (index for name, index in _MONTHS.items() if name.startswith(match.group("month").lower()[:3])), None)
                if month:
                    found.append(date(int(match.group("year")), month, int(match.group("day"))))
            else:
                year = int(match.group("y"))
                found.append(date(year + 2000 if year < 100 else year, int(match.group("m")), int(match.group("d"))))
        except ValueError:
            continue
    return found


def _duration(text: str) -> str | None:
    if _YTD.search(text):
        return "ytd"
    if _QUARTER.search(text):
        return "quarter"
    if _YEAR.search(text):
        return "fy"
    return None


def _first_data_row(table: Table) -> int:
    for index, row in enumerate(table.rows):
        if row and row_label(row[0]) and any(_amount(cell) is not None and parse_number(cell) for cell in row[1:]):
            return index
    return len(table.rows)


def _columns(table: Table) -> dict[int, tuple[list[date], str | None, str]]:
    """Each column's header: the dates in its own cells, the duration its spanning header names, and its text."""
    header = table.rows[:_first_data_row(table)]
    width = max((len(row) for row in table.rows), default=0)
    columns = {}
    for col in range(1, width):
        own = " ".join(row[col] for row in header if col < len(row) and row[col])
        spanning = []
        for row in header:
            cell = next((row[index] for index in range(min(col, len(row) - 1), 0, -1) if row[index]), "")
            if cell and (_duration(cell) or not _dates(cell)) and not _YEAR_ONLY.fullmatch(cell):
                spanning.append(cell)
        spans = " ".join(cell for cell in dict.fromkeys(spanning) if cell not in own)
        # The date may be split: "Three Months Ended September 30" over the columns, "2026" in each (Chevron's 10-Q).
        columns[col] = (_dates(own) or _dates(f"{spans} {own}"), _duration(f"{spans} {own}"), clean(f"{spans} {own}"))
    return columns


def _pick_columns(table: Table, end: date, duration: str, instant: bool) -> list[int]:
    columns = _columns(table)
    dated = {col: info for col, info in columns.items() if any(abs((day - end).days) <= 6 for day in info[0])}
    if instant:
        return sorted(col for col, info in dated.items() if info[1] in (None, "fy", "quarter"))[:1]
    wanted = sorted(col for col, info in dated.items() if info[1] == duration)
    if wanted:
        return wanted
    # A table of one duration names none in its header ("October 30, 2026 | October 31, 2025"): its dated column.
    return sorted(dated) if dated and all(info[1] is None for info in columns.values()) else []


def _anchored(document: Document, anchor: str) -> list[Table]:
    pattern = re.compile(anchor, re.I)
    tables = []
    for table in document.tables.values():
        header = " ".join(" ".join(row) for row in table.rows[:_first_data_row(table)])
        if pattern.search(clean(table.context[-400:])) or pattern.search(clean(header)):
            tables.append(table)
    return tables


def _rows(table: Table, source: TableSource) -> list[int]:
    pattern = re.compile(source.row, re.I)
    start, stop = 0, len(table.rows)
    if source.section:
        section = re.compile(source.section, re.I)
        opening = next((index for index, row in enumerate(table.rows) if row and section.fullmatch(row_label(row[0]))), None)
        if opening is None:
            return []
        start, stop, seen = opening + 1, len(table.rows), False
        # Up to the next heading once the section has figures: a labelled row ending in a colon with none ("Operating
        # income:"); a heading before its first figure is the section's own ("Net revenue:").
        for index in range(start, len(table.rows)):
            row = table.rows[index]
            figures = bool(row) and any(_amount(cell) is not None for cell in row[1:])
            if row and clean(row[0]).endswith(":") and not figures and seen:
                stop = index
                break
            seen = seen or figures
    return [index for index in range(start, stop) if table.rows[index] and pattern.fullmatch(row_label(table.rows[index][0]))]


def resolve_table(document: Document | None, source: TableSource, end: date, duration: str) -> Found:
    locator = {"anchor": source.anchor, "row_pattern": source.row, **({"section": source.section} if source.section else {})}
    if document is None:
        return Found(NONE, reason="the document could not be read", locator=locator, basis=True)
    tables = _anchored(document, source.anchor)
    if not tables:
        return Found(NONE, reason=f"no table found under {source.anchor!r} (layout changed?)", locator=locator, basis=True)
    cells, scales, without_column = [], set(), 0
    for table in tables:
        rows = _rows(table, source)
        if not rows:
            continue
        columns = [source.column] if source.column is not None else _pick_columns(table, end, duration, source.instant)
        if not columns:
            without_column += 1
            continue
        declared = table.declared_scale() or document.default_scale()
        for row in rows:
            for col in columns:
                amount = _amount(table.cell(row, col))
                if amount is None:
                    continue
                if declared and abs(declared - source.scale) > 1e-9 * source.scale:
                    scales.add(declared)
                    continue
                dates, _, header = _columns(table).get(col, ([], None, ""))
                near = min(dates, key=lambda day: abs((day - end).days), default=None)
                cells.append((round(amount * source.scale * source.sign, 2), {
                    "table": table.id, "row": row, "col": col, "row_label": row_label(table.rows[row][0]),
                    "header": header[:200], "context": clean(table.context[-120:]),
                    **({"column_date": near.isoformat()} if near else {})}))
    if scales and not cells:
        return Found(NONE, reason=f"the table now declares a scale of {sorted(scales)[0]:g}, the mapping {source.scale:g}",
                     locator=locator, basis=True)
    values = sorted({value for value, _ in cells})
    if not values:
        why = (f"no column for the {duration} to {end.isoformat()}" if without_column
               else f"no row {source.row!r} under {source.anchor!r}")
        return Found(NONE, reason=why, locator=locator, basis=bool(without_column))
    if len(values) > 1:
        return Found(MANY, reason=f"{len(values)} different cells match: {', '.join(f'{v:,.0f}' for v in values[:4])}",
                     locator={**locator, "cells": [where for _, where in cells[:4]]},
                     candidates=list(dict.fromkeys(value for value, _ in cells)))
    value, where = next(cell for cell in cells if cell[0] == values[0])
    return Found(ONE, value, locator={**locator, **where})
