"""Reads the numbers the AI pointed at, and checks them before anything is served."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .ai import GroupSpec, KpiSpec, Located, Locator, Spec
from .document import Document, clean, parse_number

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


def read_values(document: Document, spec: Spec, located: Located, default_currency: str | None) -> tuple[list[ReadValue], list[str]]:
    """Every locator resolved against the document; locators that do not hold up are dropped with a note."""
    kpis = {f"{group.key}.{kpi.key}": (group, kpi) for group in spec.groups for kpi in group.kpis}
    values: list[ReadValue] = []
    problems: list[str] = []
    seen: set[str] = set()
    for locator in located.values:
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


def _read(document: Document, locator: Locator, kpi: KpiSpec, default_currency: str | None) -> tuple[float, str | None, list[str]]:
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
        if kpi.unit in ("currency", "count") and parsed.scale_word is None:
            declared = table.declared_scale()
            if declared is None and kpi.unit == "currency":
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
        return value, _currency(text, kpi, default_currency), notes
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
    if kpi.unit == "currency" and parsed.scale_word is None and parsed.value < 1_000:
        raise LocateError(f"{value_text!r} has no amount scale")
    return parsed.value, _currency(value_text, kpi, default_currency), notes


def _currency(text: str | None, kpi: KpiSpec, default_currency: str | None) -> str | None:
    if kpi.unit not in ("currency", "ratio"):
        return None
    for marker, code in _CURRENCY_MARKERS:
        if text and marker in text:
            return code
    return default_currency if kpi.unit == "currency" else None


def _locator_record(document: Document, locator: Locator) -> dict:
    """What the next quarter's prompt is told about where this KPI was found."""
    if locator.table is not None:
        table = document.tables[locator.table]
        row = table.rows[locator.row] if locator.row < len(table.rows) else []
        label = next((cell for cell in row if cell and not re.search(r"\d", cell)), row[0] if row else "")
        return {"table": locator.table, "row": locator.row, "col": locator.col, "row_label": label[:80],
                "context": table.context[-80:]}
    return {"block": locator.block, "quote": clean(locator.quote or "")[:160], "value_text": locator.value_text}


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
            total_item = next((item for item in items if item.kpi.key == group.total_kpi), None)
            parts = [item for item in items if item.kpi.key != group.total_kpi]
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
