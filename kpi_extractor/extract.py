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
    # One gate for every reading (replay, Flash, Pro): the column's own header outranks a period the AI states.
    kept = []
    for item in values:
        mismatch = column_duration_problem(str(item.locator.get("header") or ""), located.annual)
        if mismatch:
            problems.append(f"{item.group.key}.{item.kpi.key}: {mismatch}")
        else:
            kept.append(item)
    return kept, problems


_QUARTER = re.compile(r"<3 months>|three months|<1[34] weeks>|thirteen weeks|fourteen weeks|quarter|\bq[1-4]\b|\b[1-4]q\b")
_LONGER = re.compile(r"<(6|9|12) months>|(six|nine|twelve) months|<(2[67]|39|40|5[23]) weeks>|"
                     r"(twenty-six|thirty-nine|fifty-two|fifty-three) weeks|years? ended|year to date|fiscal years?")


def column_duration_problem(header: str, annual: bool) -> str | None:
    """Why a column is not the period being read, judged from its header alone: a six-, nine- or twelve-month column
    when a quarter is expected, or a quarter's column when the year is. Silent headers pass."""
    quarter, longer = bool(_QUARTER.search(header)), bool(_LONGER.search(header))
    if not annual and longer and not quarter:
        return f"the column is headed {header!r}, not a quarter"
    if annual and quarter and not longer:
        return f"the column is headed {header!r}, not the year"
    return None


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


def _identity(text: str) -> str:
    """A label or key reduced to what it names: "3-nanometer", "3nm" and "nm3" are one node; "Net product sales" and
    "net_product_sales" one line."""
    key = member_key(text.replace("_", " "))
    key = re.sub(r"nanometers?", "nm", key)
    return re.sub(r"^nm(\d+)$", r"\1nm", key)


def _listed_kpi(group: GroupSpec, label: str) -> KpiSpec | None:
    """The KPI on the company's list a table row is, so a whole-table reading and a row-by-row reading of one table use
    one key (Flash and Pro then agree, and a breakdown is never stored twice under two names)."""
    wanted = _identity(label)
    listed = [kpi for kpi in group.kpis if kpi.key != group.total_kpi]
    matches = [kpi for kpi in listed if wanted in (_identity(kpi.label), _identity(kpi.key))]
    if not matches:
        # A row label that adds the business's name to the listed one ("UnitedHealthcare Employer & Individual -
        # Domestic" for "Employer & Individual - Domestic"): its ending, when that is specific and names one KPI.
        matches = [kpi for kpi in listed if len(_identity(kpi.label)) >= 8 and wanted.endswith(_identity(kpi.label))]
    return matches[0] if len(matches) == 1 else None


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

    def read_row(index: int, source: Table | None = None, col: int | None = None) -> tuple[str, float, str] | None:
        source, col = source or table, locator.col if col is None else col
        text = source.cell(index, col)
        parsed = parse_number(text)
        if parsed is None or index >= len(source.rows):
            return None
        label = _row_label(source.rows[index], col)
        if unit == "percent" and not parsed.percent and "%" not in header and "percent" not in header.lower():
            raise LocateError(f"{text!r} in row {index} is not a percentage")
        value = parsed.value if parsed.scale_word or unit == "percent" else parsed.value * scale
        return label, value, text

    parts: dict[str, tuple[str, float, int]] = {}
    continued: dict[str, tuple[str, int, int]] = {}  # rows read past a page break: key → (table, row, column)
    for index in range(locator.first_row, locator.last_row + 1):
        if index == locator.total_row:
            continue
        row = read_row(index)
        if row is None or not row[0]:
            continue
        key = member_key(row[0])
        if key not in parts:
            parts[key] = (row[0], row[1], index)
    total_index = locator.total_row
    total_table, total_col = table, locator.col
    if unit == "percent" and total_index is None:
        # A mix's own "Total 100%" row is its total, not a share (it would make the shares add up to 200%).
        hundred = [key for key, (_label, value, _index) in parts.items() if abs(value - 100) <= 0.5]
        others = sum(value for key, (_label, value, _index) in parts.items() if key not in hundred)
        if len(hundred) == 1 and abs(others - 100) <= MIX_TOLERANCE:
            total_index = parts.pop(hundred[0])[2]
    if total_index is None and unit == "currency" and parts:
        total_index = _total_row_by_sum(table, locator, parts, read_row)
    if total_index is None and unit == "currency" and parts:
        # The rows read stop short of a total: read on, through the table's continuation after a page break (TSMC's
        # node table runs 3nm–20nm on one page, 28nm to the total on the next), to the row that is their sum.
        reached = _read_to_total(document, table, locator, parts, read_row, continued)
        if reached:
            total_table, total_index, total_col = reached
    total_row = read_row(total_index, total_table, total_col) if total_index is not None else None
    total = total_row[1] if total_row else (100.0 if unit == "percent" else None)
    kept = remove_subtotals({key: value for key, (_label, value, _index) in parts.items()}, total)
    if total and unit == "currency" and total_index is not None and abs(sum(kept.values()) - total) > abs(total) * 0.001:
        block = _block_summing_to(parts, total_index, total)
        if block:
            kept = {key: parts[key][1] for key in block}
    if len(kept) < 2:
        raise LocateError("fewer than two rows")
    context = f"{table.context} {header}"
    currency = _currency(total_row[2] if total_row else table.cell(locator.first_row, locator.col),
                         KpiSpec(key="x", label="x", unit="currency"), default_currency,
                         context if declared_currency(context) else document.declared_currency()) if unit == "currency" else None
    # What the next quarter's replay needs: the total row's label (None when the table has none) and the scale used.
    total_label = _row_label(total_table.rows[total_index], total_col)[:80] if total_row is not None else None
    # A third quarter's report also shows the nine months to date beside the quarter; Q4 is then the year less them.
    nine = _nine_month_columns(document, table, locator, parts, continued, total_table, total_index) if unit == "currency" else {}
    values = []
    for key, (label, value, index) in parts.items():
        if key not in kept:
            continue
        source_table, source_row, source_col = continued.get(key, (locator.table, index, locator.col))
        record = {"table": source_table, "row": source_row, "col": source_col, "row_label": label[:80],
                  "context": table.context[-80:], "whole_table": True, "total_row_label": total_label,
                  "scale": scale if unit == "currency" else None,
                  "header": column_header(table, locator.first_row, locator.col)}
        if key in nine and "__total__" in nine:
            record.update({"ytd": nine[key] * scale, "ytd_total": nine["__total__"] * scale})
        values.append(ReadValue(group, _listed_kpi(group, label) or KpiSpec(key=row_key(label), label=label[:80], unit=unit),
                                value, currency, record))
    if total_row is not None:
        record = {"table": total_table.id, "row": total_index, "col": total_col, "row_label": "Total", "whole_table": True,
                  "header": column_header(table, locator.first_row, locator.col)}
        values.append(ReadValue(group, KpiSpec(key=group.total_kpi or "total", label="Total", unit=unit), total_row[1],
                                currency, record, is_total=True))
    return values


def _raw_header(table: Table, first_data_row: int, col: int) -> str:
    return clean(" ".join(row[col] for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row))).lower()


def _first_data_row(table: Table) -> int:
    """The first row with a label and a figure; a row of years ("Resolution | 2023 | 2022") is still the header."""
    def figure(cell: str) -> bool:
        return bool(cell) and parse_number(cell) is not None and not re.fullmatch(r"(19|20)\d{2}", cell.strip())
    return next((i for i, row in enumerate(table.rows) if row and re.search(r"[A-Za-z]", row[0])
                 and any(figure(cell) for cell in row[1:])), 1)


def _read_to_total(document: Document, table: Table, locator: TableLocator, parts: dict, read_row, continued: dict):
    """(table, row, column) of the total, reading on from the last row read: below it in this table, then in the next
    table when it continues this one (same column header, years included). Rows on the way join the parts; nothing is
    kept unless a row equals the sum of everything above it."""
    added: dict[str, tuple[str, float, int]] = {}
    added_from: dict[str, tuple[str, int, int]] = {}
    running = sum(value for _label, value, _index in parts.values())
    segments = [(table, locator.col, locator.last_row + 1)]
    position = document.order.index(table.id) if table.id in document.order else -1
    following = [document.tables[key] for key in document.order[position + 1:position + 4] if key in document.tables][:1]
    wanted = _raw_header(table, locator.first_row, locator.col)
    for nxt in following:
        start = _first_data_row(nxt)
        col = next((c for c in range(1, max(len(row) for row in nxt.rows)) if _raw_header(nxt, start, c) == wanted), None)
        if col is not None:
            segments.append((nxt, col, start))
    for source, col, start in segments:
        for index in range(start, min(len(source.rows), start + 30)):
            row = read_row(index, source, col)
            if row is None or not row[0]:
                continue
            if running and abs(row[1] - running) <= abs(running) * 0.001:
                for key, value in added.items():
                    parts.setdefault(key, value)
                continued.update(added_from)
                return source, index, col
            key = member_key(row[0])
            if key not in parts and key not in added:
                added[key] = (row[0], row[1], index)
                if source is not table:
                    added_from[key] = (source.id, index, col)
                running += row[1]
    return None


def _nine_month_columns(document: Document, table: Table, locator: TableLocator, parts: dict, continued: dict,
                        total_table: Table, total_index: int | None) -> dict[str, float]:
    """Each row's figure in the same year's nine-months column (raw, before scale), plus "__total__"."""
    year = re.findall(r"(?:19|20)\d{2}", _raw_header(table, locator.first_row, locator.col))

    def nine_column(source: Table) -> int | None:
        start = _first_data_row(source)
        for col in range(1, max(len(row) for row in source.rows)):
            header = _raw_header(source, start, col)
            if re.search(r"nine months|9 months", header) and (not year or year[-1] in header):
                return col
        return None

    columns = {table.id: nine_column(table)}
    found: dict[str, float] = {}
    rows = [(document.tables[continued[key][0]], continued[key][1]) if key in continued else (table, index)
            for key, (_label, _value, index) in parts.items()]
    for source, index in rows:
        col = columns.setdefault(source.id, nine_column(source))
        if col is None:
            continue
        parsed = parse_number(source.cell(index, col))
        label = _row_label(source.rows[index], col) if index < len(source.rows) else ""
        if parsed is not None and label:
            found.setdefault(member_key(label), parsed.value)
    if total_index is not None:
        col = columns.setdefault(total_table.id, nine_column(total_table))
        parsed = parse_number(total_table.cell(total_index, col)) if col is not None else None
        if parsed is not None:
            found["__total__"] = parsed.value
    return found


def _block_summing_to(parts: dict, total_index: int, total: float) -> list[str] | None:
    """The rows right next to a total row that add up to it, when the rows read span more than it covers: AMD's
    segment table lists Data Center and Embedded beside the Client and Gaming rows its "Client and Gaming" total sums."""
    ordered = sorted(parts, key=lambda key: parts[key][2])
    above = [key for key in ordered if parts[key][2] < total_index][::-1]
    below = [key for key in ordered if parts[key][2] > total_index]
    for side in (above, below):
        running, block = 0.0, []
        for key in side:
            running += parts[key][1]
            block.append(key)
            if len(block) >= 2 and abs(running - total) <= abs(total) * 0.001:
                return block
    return None


def _total_row_by_sum(table: Table, locator: TableLocator, parts: dict, read_row) -> int | None:
    """The row just below (or above) the rows read whose figure is their sum, when the reader named no total row:
    TSMC's "Net revenue" line under its geography table. Found by the numbers, never by the label alone."""
    parts_sum = sum(value for _label, value, _index in parts.values())
    for index in (locator.last_row + 1, locator.last_row + 2, locator.first_row - 1):
        if 0 <= index < len(table.rows):
            try:
                row = read_row(index)
            except LocateError:
                continue
            if row and parts_sum and abs(row[1] - parts_sum) <= abs(parts_sum) * 0.001:
                return index
    return None


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
    def heading(row: list[str]) -> str:
        # A header spanning several columns sits on the first of them ("Quarter Ended December 31," over 2022 and 2023):
        # an empty header cell takes the nearest one to its left, never the row-label column.
        for index in range(min(col, len(row) - 1), 0, -1):
            if row[index].strip():
                return row[index]
        return ""
    cells = [cell for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row) and re.search(r"[A-Za-z]", cell := heading(row))]
    text = clean(" ".join(cells)).lower()
    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                  r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", "@", text)
    # A duration names the column ("3 months", "six months", "26 weeks"), so its number is kept; dates and years blank.
    text = re.sub(r"\b(\d{1,2})(\s*-?\s*)(months?|weeks?|quarters?)\b", r"<\1 \3>", text)
    return re.sub(r"(?<![<\d])[\d.,]+(?![\d]* (?:months?|weeks?|quarters?)>)", "#", text)[:120]


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
        parts_total = sum(item.value for item in items if not (item.is_total or item.kpi.key == group.total_kpi))
        previous_total = sum(value for key, value in previous.items() if key.startswith(f"{group_key}."))
        for item in items:
            if group.kind == "revenue_breakdown" and parts_total and abs(item.value) < 0.01 * abs(parts_total):
                continue  # an immaterial row (TSMC's residual 10nm) swings wildly; the group total still checks it
            before = previous.get(f"{group_key}.{item.kpi.key}")
            if group.kind == "revenue_breakdown" and before is not None and previous_total \
                    and abs(before) < 0.01 * abs(previous_total):
                continue  # growth from an immaterial base is a launch (TSMC's 3nm: NT$0.5B to NT$29B), not an error
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
