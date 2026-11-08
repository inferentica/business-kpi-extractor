"""The three statements from a filing's printed tables, HTML or plain text, before XBRL, by rules.

A statement is found by its title just above its table ("CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except
share and per share amounts)"), which also gives its scale; a balance sheet or cash flow statement that runs over a
page break continues in the next table. Each figure column takes its period from the header above it: the date
("September 29, 2007"), or the year under a date in the title ("Three fiscal years ended September 29, 2007"), and its
span ("Three Months Ended"). Each line keeps its printed label, its indentation and its figure as printed.

It is kept only when its arithmetic holds on the printed figures (brackets for negatives):
- assets equal liabilities and equity;
- opening cash plus the change (or the three sections) equals closing cash;
- each printed total equals the lines above it, and net income follows from income before taxes.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ...checks.printed import balance_check, cash_check, income_check, total_check
from ...domain.model import Reading
from ...domain.periods import label_period
from ...domain.printed_labels import CASH_HEADING, KNOWN, PER_SHARE, SHARE_COUNT
from .dates import column_periods, statement_scale
from .find import find_statements, unlabelled_totals
from .tables import Table, figure_columns


def _concept(label: str, kind: str) -> str:
    for pattern, concept in KNOWN:
        if pattern.search(label):
            if kind != "cash_flow" and "activities" in concept.lower() + label.lower():
                continue
            if kind == "cash_flow" and "Activities" not in concept:
                continue
            return concept
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:120]
    return f"printed_{slug or 'subtotal'}"


def read_tables(all_tables: list[Table], *, accession: str, form: str, filed: date, document_end: date | None,
                fiscal_year_end: str, year_offset: int) -> tuple[list[Reading], list[str]]:
    """Every statement column of a printed filing, checked; and notes on what it could not read."""
    readings: list[Reading] = []
    notes: list[str] = []
    found = find_statements(all_tables)
    # A statement printed more than once (in full, and as a summary elsewhere): the fullest table is the statement.
    found.sort(key=lambda item: -sum(len([row for row in table.rows if row.figures]) for table in item[1]))
    for kind, group in found:
        if any(reading.statement == kind for reading in readings):
            continue
        scale = statement_scale(group)
        if scale is None:
            notes.append(f"{kind}: no single scale stated")
            continue
        periods = column_periods(kind, group, fiscal_year_end, document_end)
        if not periods:
            notes.append(f"{kind}: no dated columns")
            continue
        ordered = [periods[column] for column in sorted(periods)]
        layout = [(group[0], sorted(periods))]
        for table in group[1:]:
            own = column_periods(kind, [table], fiscal_year_end, document_end)
            columns = sorted(own) if own else [c for c in figure_columns(table)][:len(ordered)]
            if len(columns) != len(ordered):
                notes.append(f"{kind}: a continuation table's columns do not line up")
                break
            layout.append((table, columns))
        for index, period in enumerate(ordered):
            labelled = label_period(period, fiscal_year_end, year_offset)
            if labelled is None:
                notes.append(f"{kind}: column {period.key} off the calendar")
                continue
            reading = _read_column(kind, [(table, columns[index], columns) for table, columns in layout], period, scale, labelled)
            if reading is None:
                continue
            reading.accession, reading.form, reading.filed = accession, form, filed
            reading.own = document_end is not None and abs(period.end - document_end) <= timedelta(days=7)
            readings.append(reading)
    return readings, notes


def _figure(row, column: tuple[int, ...], columns: list[tuple[int, ...]]) -> float | None:
    """A row's figure in a column: at one of its positions, or a cell off them but nearer to it than to any other."""
    for position in column:
        if position in row.figures:
            return row.figures[position]
    for position, value in row.figures.items():
        nearest = min(columns, key=lambda other: min(abs(p - position) for p in other))
        if nearest == column and min(abs(p - position) for p in column) <= 2:
            return value
    return None


def _read_column(kind, layout, period, scale, labelled) -> Reading | None:
    fiscal_year, fiscal_period, duration = labelled
    lines: list[dict] = []
    printed: list[tuple[str, int, float | None]] = []  # label, level, figure as printed (None: a heading)
    named = unlabelled_totals([table for table, _column, _columns in layout]) if kind == "balance" else {}
    heading = ""
    for table, column, columns in layout:
        base = min((row.level for row in table.rows if row.label), default=0)
        for row in table.rows:
            if not row.figures and row.label:
                heading = row.label
            if not row.label and not row.figures:
                continue
            value = _figure(row, column, columns) if row.figures else None
            if row.figures and value is None:
                continue
            # A balance sheet has no per-share lines; "per share" there is a par value in a stock line's label (Kroger's
            # "Common stock, $1 par per share … 955").
            per_share = kind != "balance" and bool(PER_SHARE.search(row.label))
            shares = not per_share and bool(SHARE_COUNT.search(row.label)) and kind == "income"
            amount = None if value is None else value if per_share else value * (1 if shares else scale)
            lines.append({"line": len(lines), "concept": _concept(row.label, kind), "label": row.label[:300],
                          "level": max(0, min(20, row.level - base)), "is_heading": not row.figures, "value": amount})
            meaning = named.get(id(row), row.label)
            if re.match(r"^(balance|net\s*\(?(increase|decrease)|increase|decrease|beginning|end|start)\b", row.label, re.I) \
                    and "cash" not in row.label.lower() \
                    and CASH_HEADING.match(heading):
                # Coca-Cola: CASH AND CASH EQUIVALENTS / Balance at beginning of year; Kroger: Cash and temporary cash
                # investments: / Beginning of year
                meaning = f"{heading} {row.label}"
            if not row.figures:
                printed.append((row.label, row.level, None))
            elif not (per_share or shares):
                printed.append((meaning, row.level, value))
    lines = [line for index, line in enumerate(lines) if not line["is_heading"] or _has_lines_below(lines, index)]
    if not any(not line["is_heading"] for line in lines):
        return None
    for index, line in enumerate(lines):
        line["line"] = index
    # The checks run in printed units: a printed figure is exact to one unit of the precision it is printed to, a
    # whole million for most filers, a tenth for TSMC (where a whole-unit tolerance let 32.5 - 31.3 pass as 0.0).
    unit = _printed_unit([value for _label, _level, value in printed if value is not None])
    printed = [(label, level, None if value is None else round(value / unit, 6)) for label, level, value in printed]
    figures = [(label, level, value) for label, level, value in printed if value is not None]
    checks = [total_check(printed)]
    if kind == "balance":
        checks.insert(0, balance_check(figures))
    elif kind == "cash_flow":
        checks.insert(0, cash_check(figures))
    else:
        checks.insert(0, income_check(figures))
    return Reading(statement=kind, duration=duration, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                   period_end=period.end, period_start=period.start, currency=None, accession="", form="", filed=date.min,
                   own=False, source="html", lines=lines, checks=checks)


def _printed_unit(values: list[float]) -> float:
    """The smallest place the column's figures are printed to: 1, 0.1, 0.01 or 0.001."""
    for places in range(4):
        if all(abs(value * 10 ** places - round(value * 10 ** places)) < 1e-6 for value in values):
            return 10.0 ** -places
    return 0.001


def _has_lines_below(lines: list[dict], index: int) -> bool:
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
