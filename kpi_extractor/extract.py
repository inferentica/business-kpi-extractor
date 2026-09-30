"""Reads the numbers the AI pointed at, and checks them before anything is served."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .ai import GroupSpec, KpiSpec, Located, Locator, Spec, TableLocator
from .document import Document, Table, clean, declared_currency, parse_number
from .xbrl import member_key, remove_subtotals

MIX_TOLERANCE = 2.5  # percentage points; each rounded share can be off by half a point
BREAKDOWN_TOLERANCE = 0.01
PERIOD_TOLERANCE_DAYS = 12
MAX_RATIO_JUMP = 3.0
MAX_MIX_JUMP = 30.0
_CURRENCY_MARKERS = (("NT$", "TWD"), ("US$", "USD"), ("HK$", "HKD"), ("C$", "CAD"), ("A$", "AUD"), ("RMB", "CNY"),
                     ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("$", "USD"))


class LocateError(ValueError):
    pass


@dataclass
class ReadValue:
    group: GroupSpec
    kpi: KpiSpec
    value: float
    currency: str | None
    locator: dict
    notes: list[str] = field(default_factory=list)
    status: str = "verified"
    # The breakdown's total row: its reconciliation target, never stored as a part.
    is_total: bool = False


def read_values(document: Document, spec: Spec, located: Located, default_currency: str | None) -> tuple[list[ReadValue], list[str]]:
    """Every locator resolved against the document; locators that do not hold up are dropped with a note."""
    kpis = {f"{group.key}.{kpi.key}": (group, kpi) for group in spec.groups for kpi in group.kpis}
    groups = {group.key: group for group in spec.groups}
    values: list[ReadValue] = []
    problems: list[str] = []
    seen: set[str] = set()
    table_groups: set[str] = set()
    for locator in located.tables:
        group = groups.get(locator.group)
        if group is None or group.kind == "metric" or locator.group in table_groups:
            continue
        try:
            rows = _read_table(document, locator, group, default_currency)
        except LocateError as error:
            problems.append(f"{locator.group}: {error}")
            continue
        table_groups.add(locator.group)
        values.extend(rows)
    for locator in located.values:
        if locator.kpi.split(".", 1)[0] in table_groups:
            continue  # the whole table was read for this group
        if locator.kpi not in kpis or locator.kpi in seen:
            continue
        group, kpi = kpis[locator.kpi]
        try:
            value, currency, notes = _read(document, locator, kpi, default_currency)
        except LocateError as error:
            problems.append(f"{locator.kpi}: {error}")
            continue
        seen.add(locator.kpi)
        values.append(ReadValue(group, kpi, value, currency, _locator_record(document, locator), notes))
    return values, problems


_PEOPLE = re.compile(r"employee|headcount|workforce|staff", re.I)


def _read(document: Document, locator: Locator, kpi: KpiSpec, default_currency: str | None) -> tuple[float, str | None, list[str]]:
    value, currency, notes = _read_raw(document, locator, kpi, default_currency)
    if kpi.unit == "count" and _PEOPLE.search(kpi.label) and value > 5_000_000:
        raise LocateError(f"{kpi.label} of {value:,.0f} is not a plausible headcount")
    return value, currency, notes


def _read_raw(document: Document, locator: Locator, kpi: KpiSpec, default_currency: str | None) -> tuple[float, str | None, list[str]]:
    notes: list[str] = []
    if locator.table is not None:
        table = document.tables.get(locator.table)
        if table is None:
            raise LocateError(f"unknown table {locator.table}")
        text = table.cell(locator.row, locator.col)
        parsed = parse_number(text)
        if parsed is None:
            raise LocateError(f"no number in {locator.table} r{locator.row} c{locator.col}")
        header = " ".join(" ".join(row) for row in table.rows[:3])
        if kpi.unit == "percent" and not parsed.percent and not re.search(r"%|\bpercent", f"{' '.join(table.rows[locator.row])} {header}", re.I):
            raise LocateError(f"{text!r} is not a percentage")
        value = parsed.value
        if kpi.unit == "count" and parsed.scale_word is None:
            # A table's "(in millions, except employee data)" describes its amounts, not its counts: a count takes a
            # scale only from its own row label ("Paid memberships (in millions)") or from the reading.
            row_scale = _row_scale(" ".join(table.rows[locator.row]))
            value *= row_scale or (locator.scale if locator.scale in (1, 1e3, 1e6, 1e9) else 1)
            return value, None, notes
        if kpi.unit == "currency" and parsed.scale_word is None:
            declared = table.declared_scale()
            if declared is None:
                declared = document.default_scale()
            if declared is not None:
                if locator.scale not in (None, declared):
                    notes.append(f"table declares scale {declared:g}, AI said {locator.scale:g}")
                value *= declared
            elif locator.scale in (1, 1e3, 1e6, 1e9):
                value *= locator.scale
                if locator.scale != 1:
                    notes.append("scale read by AI")
            else:
                raise LocateError("amount scale unknown")
        context = f"{table.context} {' '.join(' '.join(row) for row in table.rows[:4])}"
        return value, _currency(text, kpi, default_currency, context), notes
    block = document.blocks.get(locator.block or "")
    if block is None:
        raise LocateError(f"unknown text block {locator.block}")
    quote, value_text = clean(locator.quote or ""), clean(locator.value_text or "")
    if quote not in block.text:
        raise LocateError("quote not found in the document")
    if value_text not in quote:
        raise LocateError("value_text is not inside the quote")
    parsed = parse_number(value_text)
    if parsed is None:
        raise LocateError(f"no number in {value_text!r}")
    if kpi.unit == "percent" and not parsed.percent and not re.search(r"\bpercent\b", quote, re.I):
        raise LocateError(f"{value_text!r} is not a percentage")
    value = parsed.value
    if kpi.unit == "currency" and parsed.scale_word is None:
        declared = block.declared_scale() or document.default_scale()
        if declared is None and locator.scale in (1, 1e3, 1e6, 1e9):
            declared = locator.scale
            notes.append("scale read by AI")
        if declared is not None:
            value *= declared
        elif value < 1_000:
            raise LocateError(f"{value_text!r} has no amount scale")
    return value, _currency(f"{value_text} {quote}", kpi, default_currency, block.text), notes


def row_key(label: str) -> str:
    """A table row's KPI key: its label normalized the same way across filings ("3-nanometer" → "r3nanometer")."""
    key = member_key(label)
    return (key if key[:1].isalpha() else f"r{key}")[:48] or "row"


_ROW_SCALE = re.compile(r"\((?:in\s+)?(?P<word>thousands|millions|billions)\)", re.I)


def _row_scale(text: str) -> float | None:
    match = _ROW_SCALE.search(text)
    return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[match.group("word").lower()] if match else None


def _read_table(document: Document, locator: TableLocator, group: GroupSpec, default_currency: str | None) -> list[ReadValue]:
    """Every row of a breakdown or mix table in the reported column, labelled by its own row label. Rows that are the
    sum of other rows (an "advanced technologies" subtotal) are dropped, so each part is counted once."""
    table = document.tables.get(locator.table)
    if table is None:
        raise LocateError(f"unknown table {locator.table}")
    unit = "percent" if group.kind == "mix" else "currency"
    header = " ".join(" ".join(row) for row in table.rows[:3])
    scale = 1.0
    if unit == "currency":
        scale = table.declared_scale() or document.default_scale() or (
            locator.scale if locator.scale in (1, 1e3, 1e6, 1e9) else None)
        if scale is None:
            raise LocateError("amount scale unknown")

    def read_row(index: int) -> tuple[str, float, str] | None:
        text = table.cell(index, locator.col)
        parsed = parse_number(text)
        if parsed is None or index >= len(table.rows):
            return None
        label = _row_label(table.rows[index], locator.col)
        if unit == "percent" and not parsed.percent and "%" not in header and "percent" not in header.lower():
            raise LocateError(f"{text!r} in row {index} is not a percentage")
        value = parsed.value if parsed.scale_word or unit == "percent" else parsed.value * scale
        return label, value, text

    parts: dict[str, tuple[str, float, int]] = {}
    for index in range(locator.first_row, locator.last_row + 1):
        if index == locator.total_row:
            continue
        row = read_row(index)
        if row is None or not row[0]:
            continue
        key = member_key(row[0])
        if key not in parts:
            parts[key] = (row[0], row[1], index)
    total_row = read_row(locator.total_row) if locator.total_row is not None else None
    total = total_row[1] if total_row else (100.0 if unit == "percent" else None)
    kept = remove_subtotals({key: value for key, (_label, value, _index) in parts.items()}, total)
    if len(kept) < 2:
        raise LocateError("fewer than two rows")
    context = f"{table.context} {header}"
    currency = _currency(total_row[2] if total_row else table.cell(locator.first_row, locator.col),
                         KpiSpec(key="x", label="x", unit="currency"), default_currency,
                         context if declared_currency(context) else document.declared_currency()) if unit == "currency" else None
    # What the next quarter's replay needs: the total row's label (None when the table has none) and the scale used.
    total_label = _row_label(table.rows[locator.total_row], locator.col)[:80] if total_row is not None else None
    values = []
    for key, (label, value, index) in parts.items():
        if key not in kept:
            continue
        record = {"table": locator.table, "row": index, "col": locator.col, "row_label": label[:80],
                  "context": table.context[-80:], "whole_table": True, "total_row_label": total_label,
                  "scale": scale if unit == "currency" else None,
                  "header": column_header(table, locator.first_row, locator.col)}
        values.append(ReadValue(group, KpiSpec(key=row_key(label), label=label[:80], unit=unit), value, currency, record))
    if total_row is not None:
        record = {"table": locator.table, "row": locator.total_row, "col": locator.col, "row_label": "Total", "whole_table": True}
        values.append(ReadValue(group, KpiSpec(key=group.total_kpi or "total", label="Total", unit=unit), total_row[1],
                                currency, record, is_total=True))
    return values


def _currency(text: str | None, kpi: KpiSpec, default_currency: str | None, context: str = "") -> str | None:
    """A value's currency, most specific evidence first: a marker on the value itself (NT$, US$, €), then what its
    table or passage declares, then the company's reporting currency. A bare "$" is only a dollar sign: in TSMC's
    reports it means New Taiwan dollars, so it defers to the declaration and the reporting currency."""
    if kpi.unit not in ("currency", "ratio"):
        return None
    for marker, code in _CURRENCY_MARKERS:
        if marker != "$" and text and marker in text:
            return code
    declared = declared_currency(context)
    if declared:
        return declared
    if text and "$" in text:
        return default_currency or "USD"
    return default_currency if kpi.unit == "currency" else None


def column_header(table: Table, first_data_row: int, col: int) -> str:
    """The words heading a column ("Three Months Ended @ #") with dates and numbers blanked, so a quarter's column and a
    six-month or prior-year column never pass for one another when a reading is replayed."""
    cells = [row[col] for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row) and re.search(r"[A-Za-z]", row[col])]
    text = clean(" ".join(cells)).lower()
    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                  r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", "@", text)
    return re.sub(r"[\d.,]+", "#", text)[:120]


def _locator_record(document: Document, locator: Locator) -> dict:
    """What the next quarter's prompt is told about where this KPI was found."""
    if locator.table is not None:
        table = document.tables[locator.table]
        row = table.rows[locator.row] if locator.row < len(table.rows) else []
        return {"table": locator.table, "row": locator.row, "col": locator.col,
                "row_label": _row_label(row, locator.col)[:80], "context": table.context[-80:], "scale": locator.scale,
                "header": column_header(table, locator.row, locator.col)}
    return {"block": locator.block, "quote": clean(locator.quote or "")[:160], "value_text": locator.value_text}


def _row_label(row: list[str], col: int) -> str:
    """A row's label: the first cell with words to the left of the value (not a "$ -" filler in another column)."""
    return next((cell for cell in row[:col] if re.search(r"[A-Za-z]", cell)),
                next((cell for cell in row if re.search(r"[A-Za-z]", cell) and not re.search(r"\d", cell)), ""))


def locator_hint(locator: dict | None) -> str | None:
    if not locator:
        return None
    if "quote" in locator:
        return f"text like {locator['quote']!r}"
    return f"table row {locator.get('row_label')!r}"


def check_period(located: Located, expected: date) -> str | None:
    try:
        reported = date.fromisoformat(str(located.period_end)[:10])
    except ValueError:
        return "reported period missing"
    if abs((reported - expected).days) > PERIOD_TOLERANCE_DAYS:
        return f"reported period {reported} does not match the quarter ended {expected}"
    return None


def validate_groups(values: list[ReadValue], previous: dict[str, float]) -> None:
    """Marks every value of a group that fails its check as needs_review, with the reason."""
    by_group: dict[str, list[ReadValue]] = {}
    for item in values:
        by_group.setdefault(item.group.key, []).append(item)
    for group_key, items in by_group.items():
        group = items[0].group
        reason = None
        if group.kind == "mix":
            if any(not 0 <= item.value <= 100 for item in items):
                reason = "share outside 0-100%"
            else:
                total = sum(item.value for item in items)
                if abs(total - 100) > MIX_TOLERANCE:
                    reason = f"shares add up to {total:.1f}%"
        elif group.kind == "revenue_breakdown":
            total_item = next((item for item in items if item.is_total or item.kpi.key == group.total_kpi), None)
            parts = [item for item in items if not (item.is_total or item.kpi.key == group.total_kpi)]
            if total_item is None or not total_item.value:
                reason = "no total revenue to reconcile against"
            elif len(parts) < 2:
                reason = "fewer than two parts"
            else:
                error = (sum(item.value for item in parts) - total_item.value) / total_item.value
                if abs(error) > BREAKDOWN_TOLERANCE:
                    reason = f"parts differ from the total by {error:.1%}"
        if reason:
            for item in items:
                item.status = "needs_review"
                item.notes.append(reason)
        for item in items:
            before = previous.get(f"{group_key}.{item.kpi.key}")
            jump = _jump(item, before)
            if jump:
                item.status = "needs_review"
                item.notes.append(jump)


def _jump(item: ReadValue, before: float | None) -> str | None:
    if before is None:
        return None
    if item.group.kind == "mix":
        return f"moved {item.value - before:+.1f} points in a quarter" if abs(item.value - before) > MAX_MIX_JUMP else None
    if item.kpi.unit == "percent" or before <= 0 or item.value <= 0:
        return None
    ratio = item.value / before
    return f"changed {ratio:.1f}x in a quarter" if ratio > MAX_RATIO_JUMP or ratio < 1 / MAX_RATIO_JUMP else None


def describe(document: Document, item: ReadValue) -> str:
    """What a reading points at, for a reviewer: the value with its table row and column headers, or its quote."""
    locator = item.locator
    if "table" in locator:
        table = document.tables[locator["table"]]
        column = locator["col"]
        headers = [row[column] for row in table.rows[:locator["row"]][:5] if column < len(row) and row[column]]
        cell = table.cell(locator["row"], column) or ""
        return (f"{cell!r} in {locator['table']} row {locator.get('row_label')!r}, column {' / '.join(headers)!r}"
                f" (read as {item.value:g})")
    return f"{locator.get('value_text')!r} in {locator.get('block')}: {locator.get('quote')!r} (read as {item.value:g})"
