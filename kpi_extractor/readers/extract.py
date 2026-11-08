"""Reads the numbers the AI pointed at."""
from __future__ import annotations

import re
from datetime import date, timedelta
from dataclasses import dataclass, field

from ..ai import GroupSpec, KpiSpec, Located, Locator, Spec, TableLocator
from ..quality.checks import MIX_TOLERANCE
from ..documents.document import _DECLARED_CURRENCIES, Document, Table, TextBlock, clean, declared_currency, parse_number, word_number
from ..xbrl import member_key, remove_subtotals
from ..rules.scale import (  # noqa: F401 - re-exported for callers of extract
    _ai_scale_notes, _ANY_SCALE, _BARE_SCALE, _COLUMN_SCALE, _column_scale, _PER_UNIT,
    _PER_UNIT_KPI, _preceding_scale, _ROW_SCALE, _row_scale, _ROW_SCALE_SHORT, _SCALE_OF,
    _scale_quoted, _scale_word, _SCALE_WORDS_OF, SCALE_NOT_STATED)
from ..rules.table import (  # noqa: F401 - re-exported for callers of extract
    _heading, _nearest_heading, _section_heading)
from ..rules.wording import (  # noqa: F401 - re-exported for callers of extract
    _CAUSE, _COMPARED, _compares_to_a_small_change, _FALL, _FALL_WORD, _figure_forms,
    _FORWARD, _NUMBER, _PERIOD_LIST, _PLAIN_WORDS, explanation_supports, falls,
    forward_looking, listed_after_fall, net_gross_mismatch, previous_from_jump, SIGN_NOT_STATED)
from ..rules.currency import (  # noqa: F401 - re-exported for callers of extract
    _column_currency, _currency, _CURRENCY_MARKERS)
from ..rules.period import (  # noqa: F401 - re-exported for callers of extract
    _CHANGE_KPI, _FOURTH_QUARTER, _FULL_DATE, _LONG_SPAN_KPI, _LONGER, _MONTH_NAME,
    _MONTH_SPAN, _MONTHS, _NAMES_QUARTER, _NUMERIC_DATE, _ORDINAL, _OTHER_PERIOD,
    _QUARTER, _stands_at_period_end, _STOCK_KPI, _YEAR, _YEAR_ROW, _YTD_TITLE,
    column_dates, column_duration_problem, column_header, header_contradicts)
from ..rules.identity import (  # noqa: F401 - re-exported for callers of extract
    _combined_line, _FOOTNOTE, _identity, _label_words, _listed_kpi, _NOT_THE_PLACE,
    _PLACE, _place_not_named, _places, _plain_label, _row_label, _row_named_twice,
    _SPACED_FOOTNOTE, _SUB_ROW, _tail, _TOTAL_LABEL, _TWIN_REACH, labels_agree,
    PLACE_NOT_NAMED, row_key, ROW_NAMED_TWICE)


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
            if locator.table is not None and locator.table in document.tables:
                table = document.tables[locator.table]
                row_label = _row_label(table.rows[locator.row], locator.col) if locator.row < len(table.rows) else ""
                if not labels_agree(locator.row_label, row_label):
                    raise LocateError(f"row mismatch: {locator.table} r{locator.row} is {row_label!r}, not the "
                                      f"{locator.row_label!r} you named")
                # A part of a breakdown or mix pointed at a combined line (Total Markets & Securities Services for
                # Securities Services). A metric is often reported as its own total ("Total assets under management").
                if group.kind != "metric" and _combined_line(row_label, kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the combined line {row_label!r}, not "
                                      f"{kpi.label!r}: point at the row for {kpi.label} itself")
                if other := net_gross_mismatch(row_label, kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the {other} figure {row_label!r}, not {kpi.label!r}")
                row_cells = table.rows[locator.row] if locator.row < len(table.rows) else []
                first_cell = (row_cells[0] if row_cells else "").strip()  # a bare year has no letters
                if not located.annual and _YEAR_ROW.match(first_cell) and not _LONG_SPAN_KPI.search(kpi.label):
                    raise LocateError(f"{locator.table} r{locator.row} is the year {first_cell!r}, not a quarter")
            # A count is never read from a column a currency heads: Auna's "2Q'25 (USD)" column beside its quarter's
            # gave 17 (thousand) patients treated for the quarter's 54.
            if (kpi.unit == "count" and locator.table is not None and locator.table in document.tables
                    and _column_currency(document.tables[locator.table], locator.row, locator.col, None)):
                raise LocateError(f"{locator.table} c{locator.col} is a currency's column, not the count's")
            value, currency, notes = _read(document, locator, kpi, default_currency)
            if locator.table is not None and locator.table in document.tables and group.kind == "metric":
                if unnamed := _place_not_named(kpi.label, document.tables[locator.table], locator.row, locator.col):
                    notes.append(f"{PLACE_NOT_NAMED}: {unnamed}")
            # Any group's row: Auna's eliminations are a line of its revenue by segment.
            if locator.table is not None and locator.table in document.tables:
                if twin := _row_named_twice(document.tables[locator.table], locator.row, locator.col):
                    notes.append(f"{ROW_NAMED_TWICE}: {twin}")
        except LocateError as error:
            problems.append(f"{locator.kpi}: {error}")
            continue
        seen.add(locator.kpi)
        held = SCALE_NOT_STATED in notes or SIGN_NOT_STATED in notes or any(note.startswith((PLACE_NOT_NAMED, ROW_NAMED_TWICE)) for note in notes)
        values.append(ReadValue(group, kpi, value, currency, _locator_record(document, locator), notes,
                                status="needs_review" if held else "verified"))
    # One gate for every reading (replay, Flash, the reasoning reader): the column's own header outranks a period the AI states.
    kept = []
    for item in values:
        mismatch = column_duration_problem(str(item.locator.get("header") or ""), located.annual)
        if mismatch and _stands_at_period_end(item.kpi) and (not located.annual or _FOURTH_QUARTER.search(mismatch)):
            # A level is no span's total: Auna's beds "YTD 25" at September 30 are the beds at the quarter's end, and
            # Scotiabank's deposits in its "Q4" column are the year-end deposits (its Q4 ratios are not the year's).
            mismatch = None
        if mismatch:
            problems.append(f"{item.group.key}.{item.kpi.key}: {mismatch}")
        else:
            kept.append(item)
    return kept, problems


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
        if kpi.unit in ("currency", "count") and parsed.percent:
            raise LocateError(f"{text!r} is a percentage, not an amount")
        header = " ".join(" ".join(row) for row in table.rows[:3])
        if kpi.unit == "percent" and not parsed.percent and not re.search(
                r"%|\bpercent", f"{' '.join(table.rows[locator.row])} {header} {_section_heading(table, locator.row)}", re.I):
            raise LocateError(f"{text!r} is not a percentage")
        value = parsed.value
        if kpi.unit == "count" and parsed.scale_word is None:
            # A table's "(in millions, except employee data)" describes its amounts, not its counts: a count takes a
            # scale only from its own row label ("Paid memberships (in millions)"), its column's ("Purchase Trans.
            # (Mil.)"), or a scale the reading quotes from the document.
            # FedEx's "Avg. daily package volume (ADV) (000s):" heads the rows under it: the nearest heading's scale is
            # theirs, never one further up ("Revenue: (in thousands)" over MP Materials' tonnages).
            row_scale = (_row_scale(" ".join(table.rows[locator.row])) or _column_scale(table, locator.row, locator.col)
                         or _row_scale(_nearest_heading(table, locator.row)))
            if row_scale is None and locator.scale in (1e3, 1e6, 1e9):
                if _scale_quoted(document, table, locator.scale_text, locator.scale):
                    row_scale = locator.scale
                    notes.append(f"scale quoted: {clean(locator.scale_text or '')[:40]!r}")
                else:
                    notes.append(SCALE_NOT_STATED)
                    row_scale = locator.scale
            value *= row_scale or 1
            return value, None, notes
        if kpi.unit == "currency" and parsed.scale_word is None:
            # A row that states its own scale overrides the table's: JPM's "Debit and credit card sales volume (in
            # billions)" in a table "(in millions)".
            own = _row_scale(" ".join(table.rows[locator.row])) or _column_scale(table, locator.row, locator.col) \
                or (1.0 if _PER_UNIT_KPI.search(kpi.label) else None)
            declared = own or table.declared_scale()
            if declared is None:
                declared = document.default_scale()
            if own is None and declared is not None and locator.scale in (1e3, 1e6, 1e9) and locator.scale != declared \
                    and _scale_quoted(document, table, locator.scale_text, locator.scale):
                # Wording the reader quotes from beside the table beats a caption the table only inherits from the
                # one before it: Goldman's "$ in billions" assets under supervision after its VaR table "$ in millions".
                notes.append(f"scale quoted: {clean(locator.scale_text or '')[:40]!r} over the table's {declared:g}")
                value *= locator.scale
            elif declared is not None:
                if locator.scale not in (None, declared):
                    notes.append(f"table declares scale {declared:g}, AI said {locator.scale:g}")
                value *= declared
            elif locator.scale in (1, 1e3, 1e6, 1e9):
                value *= locator.scale
                notes.extend(_ai_scale_notes(document, table, locator.scale_text, locator.scale, value))
            else:
                raise LocateError("amount scale unknown")
        context = f"{table.context} {' '.join(' '.join(row) for row in table.rows[:4])}"
        return value, _column_currency(table, locator.row, locator.col, text) or _currency(text, kpi, default_currency, context), notes
    block = document.blocks.get(locator.block or "") or _table_as_text(document, locator.block)
    if block is None:
        raise LocateError(f"unknown text block {locator.block}")
    quote, value_text = clean(locator.quote or ""), clean(locator.value_text or "")
    if quote not in block.text:
        raise LocateError("quote not found in the document")
    if value_text not in quote:
        raise LocateError("value_text is not inside the quote")
    parsed = parse_number(value_text) or (word_number(value_text) if kpi.unit == "count" else None)
    if parsed is None:
        raise LocateError(f"no number in {value_text!r}")
    if kpi.unit == "percent" and not parsed.percent and not re.search(r"\bpercent\b", quote, re.I):
        raise LocateError(f"{value_text!r} is not a percentage")
    if kpi.unit in ("currency", "count") and parsed.percent:
        # TSMC's results-day slide gives node shares ("0.15/0.18um 2%"), never amounts: a share is not this KPI.
        raise LocateError(f"{value_text!r} is a percentage, not an amount")
    value = parsed.value
    before = quote[:quote.find(value_text)]
    if forward_looking(before):
        raise LocateError("the quote is a forecast or guidance, not the reported figure")
    # "YTD 2025, the company was awarded 14 new contract commitments" is the year to date, not the quarter's count,
    # unless the KPI itself is defined over the longer span (Tesla's cumulative deliveries, PGIM's trailing flows).
    # A quote that names the quarter ("In the second quarter of fiscal 2026, 2,148 units booked") is the quarter's figure.
    if (span := _OTHER_PERIOD.search(before[-120:])) and not _LONG_SPAN_KPI.search(kpi.label) \
            and not _NAMES_QUARTER.search(before[-160:]):
        # GE Vernova's "backlog increased $6.5 billion year-to-date, to approximately $26 billion" states a change over
        # the year and then the level at quarter end: the level of a stock measure is the quarter's figure.
        between = before[-120:][span.end():]
        level = re.search(r"\b(?:to|reached|reaching|stood at|stands at|as of|at the end|ended the quarter with|now)\b", between)
        if not (level and _STOCK_KPI.search(kpi.label)):
            raise LocateError(f"the quote gives the figure {span.group(0).lower()!r}, not the quarter's")
    # A slide's title can follow its figures: Borr's "22 New Commitments $625M Added Backlog Revenue ... 4,820+ Backlog
    # Days 2025 YTD Contracting Statistics" gives the year to date, though no word before the figures says so.
    # A quote that ends its sentence, and a next bullet or sentence ("Through the first six months of 2025, ..."), is no
    # such title.
    heading = re.split(r"[.;:!?]\s|\u2022|\n", block.text[block.text.find(quote) + len(quote):][:60])[0]
    if not re.search(r"[.!?]$", quote.strip()) and (span := _YTD_TITLE.search(heading)) and not _LONG_SPAN_KPI.search(kpi.label) \
            and not _NAMES_QUARTER.search(heading) and not _stands_at_period_end(kpi):
        raise LocateError(f"the heading after the quote gives the figures {span.group(0).lower()!r}, not the quarter's")
    if other := net_gross_mismatch(before[-80:], kpi.label):
        raise LocateError(f"the quote gives the {other} figure, not {kpi.label!r}")
    if value > 0 and (kpi.unit == "percent" or (kpi.unit == "count" and _CHANGE_KPI.search(kpi.label))) \
            and falls(quote, value_text):
        # "The average price per ad decreased by 16%" is a change of -16%, and Vodafone's "contract customer base
        # declined by 46,000" is net additions of -46,000: the sentence carries the sign, not the number.
        value = -value
        notes.append("a fall, as the quote says")
    elif value > 0 and kpi.unit == "count" and _CHANGE_KPI.search(kpi.label) and listed_after_fall(quote, value_text):
        # Vodafone's "declined by 127,000 during the year (Q3: 73,000, Q4: 22,000)" means two falls, but its "declined
        # by 6,000 (Q2: 62,000)" lists a gain: an unsigned figure in such a list has no sign a reader can trust.
        notes.append(SIGN_NOT_STATED)
    if kpi.unit == "count" and parsed.scale_word is None:
        # A count takes its label's own scale, as a table row's does: "Starlink Subscribers (#, in millions) 12.0".
        value *= _row_scale(quote[:quote.find(value_text)]) or 1
    if kpi.unit == "currency" and parsed.scale_word is None:
        # The last scale heading before the quote in its block: Apollo's highlights page heads its earnings "($ in
        # millions …)" and, further on, its AUM and business drivers "($ in billions)".
        # A per-unit amount keeps its units, as in a table: UTI's "$7,300" revenue per student under "($ in millions,
        # except revenue per student amounts)".
        declared = 1.0 if _PER_UNIT_KPI.search(kpi.label) else \
            _preceding_scale(block.text, block.text.find(quote)) or block.declared_scale() or document.default_scale()
        if declared is None and locator.scale in (1, 1e3, 1e6, 1e9):
            declared = locator.scale
            notes.extend(_ai_scale_notes(document, block, locator.scale_text, locator.scale, value * declared))
        if declared is not None:
            value *= declared
        elif value < 1_000:
            raise LocateError(f"{value_text!r} has no amount scale")
    return value, _currency(f"{value_text} {quote}", kpi, default_currency, block.text), notes


def _table_as_text(document: Document, key: str | None) -> TextBlock | None:
    """A table quoted as text: a release's highlight bullets laid out as one-row tables ("•" | "Family daily active
    people (DAP) – DAP was 2.26 billion…", Meta 2017–2020) are sentences, and a quote of one is read like a block's."""
    table = document.tables.get(key or "")
    if table is None:
        return None
    return TextBlock(table.id, clean(" ".join(cell for row in table.rows for cell in row if cell)))


def _read_table(document: Document, locator: TableLocator, group: GroupSpec, default_currency: str | None) -> list[ReadValue]:
    """Every row of a breakdown or mix table in the reported column, labelled by its own row label. Rows that are the
    sum of other rows (an "advanced technologies" subtotal) are dropped, so each part is counted once."""
    table = document.tables.get(locator.table)
    if table is None:
        raise LocateError(f"unknown table {locator.table}")
    for index, named in ((locator.first_row, locator.first_label), (locator.last_row, locator.last_label)):
        actual = _row_label(table.rows[index], locator.col) if 0 <= index < len(table.rows) else ""
        if not labels_agree(named, actual):
            raise LocateError(f"row mismatch: {locator.table} r{index} is {actual!r}, not the {named!r} you named")
    unit = "percent" if group.kind == "mix" else "currency"
    header = " ".join(" ".join(row) for row in table.rows[:3])
    scale = 1.0
    if unit == "currency":
        scale = _column_scale(table, locator.first_row, locator.col) or table.declared_scale() or document.default_scale()
        if scale is None and locator.scale in (1, 1e3, 1e6, 1e9):
            scale = locator.scale
            if locator.scale != 1 and not _scale_quoted(document, table, locator.scale_text, locator.scale):
                raise LocateError(f"scale {locator.scale:g} is not stated in the table: quote the words that state it in scale_text")
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
        own = _row_scale(" ".join(source.rows[index])) if unit == "currency" else None  # a row's own "(in billions)" wins
        value = parsed.value if parsed.scale_word or unit == "percent" else parsed.value * (own or scale)
        return label, value, text

    parts: dict[str, tuple[str, float, int]] = {}
    continued: dict[str, tuple[str, int, int]] = {}  # rows read past a page break: key → (table, row, column)
    labelled_totals: list[int] = []
    for index in range(locator.first_row, locator.last_row + 1):
        if index == locator.total_row:
            continue
        row = read_row(index)
        if row is None or not row[0]:
            continue
        if _TOTAL_LABEL.match(row[0]):
            # "Total Markets & Securities Services" is a subtotal by its own name, whatever the signs of its parts
            # (JPMorgan's includes a negative "Credit Adjustments & Other", which a sum check of positive rows misses).
            labelled_totals.append(index)
            continue
        key = member_key(row[0])
        if key not in parts:
            parts[key] = (row[0], row[1], index)
    total_index = locator.total_row
    if total_index is None and labelled_totals and labelled_totals[-1] == locator.last_row:
        total_index = labelled_totals[-1]  # the table's closing "Total" row is its total
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
    # A subtotal the list names as a parent (the owner's Total Optum over Optum Health, Insight and Rx) is kept beside its
    # lines: they are checked against it.
    parents = {kpi.parent for kpi in group.kpis if kpi.parent}
    for key in parents & set(parts):
        kept.setdefault(key, parts[key][1])
    # A subtotal the list names as a parent but that the table labels as a total ("Total noninterest revenue", JPMorgan's
    # 2016-2017 supplements) is that parent, not a closing total.
    named_parents = {kpi.parent for kpi in group.kpis if kpi.parent}
    for index in labelled_totals:
        row = read_row(index) if index != total_index else None
        kpi = _listed_kpi(group, row[0]) if row else None
        if kpi is not None and kpi.key in named_parents and member_key(row[0]) not in parts:
            parts[member_key(row[0])] = (row[0], row[1], index)
            kept[member_key(row[0])] = row[1]
    parent_of = _place_unlisted_rows(group, parts, kept, total)
    if len(kept) < 2:
        raise LocateError("fewer than two rows")
    context = f"{table.context} {header}"
    currency = _column_currency(table, locator.first_row, locator.col, table.cell(locator.first_row, locator.col)) if unit == "currency" else None
    currency = currency or _currency(total_row[2] if total_row else table.cell(locator.first_row, locator.col),
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
                  "header": column_header(table, locator.first_row, locator.col),
                  "row_numbers": row_numbers(document.tables.get(source_table), source_row, source_col)}
        if key in nine and "__total__" in nine:
            record.update({"ytd": nine[key] * scale, "ytd_total": nine["__total__"] * scale})
        values.append(ReadValue(group, _listed_kpi(group, label) or KpiSpec(key=row_key(label), label=label[:80], unit=unit,
                                                                            parent=parent_of.get(key)),
                                value, currency, record))
    if total_row is not None:
        record = {"table": total_table.id, "row": total_index, "col": total_col, "row_label": "Total", "whole_table": True,
                  "header": column_header(table, locator.first_row, locator.col)}
        values.append(ReadValue(group, KpiSpec(key=group.total_kpi or "total", label="Total", unit=unit), total_row[1],
                                currency, record, is_total=True))
    return values


def _place_unlisted_rows(group: GroupSpec, parts: dict[str, tuple[str, float, int]], kept: dict[str, float],
                         total: float | None) -> dict[str, str]:
    """Rows of a whole table the list does not name, where the list's own top level already adds up to the total. A row
    inside a listed subtotal's block is one of its lines (JPMorgan's "Securities gains" among the noninterest revenue
    lines) and is placed under it. A row outside every such block can't be a missing part, since the top level is
    complete, so it is left out: JPMorgan's interest income and interest expense, which only make up net interest income.
    Returns the parent of each row placed; changes nothing when the listed rows do not add up on their own."""
    if not total or group.kind != "revenue_breakdown":
        return {}
    listed = {key: kpi for key in kept if (kpi := _listed_kpi(group, parts[key][0])) is not None}
    if not any(kpi.parent for kpi in listed.values()):
        return {}
    top = [key for key, kpi in listed.items() if not kpi.parent and kpi.key != group.total_kpi]
    if len(top) < 2 or abs(sum(kept[key] for key in top) - total) > abs(total) * 0.001:
        return {}
    blocks: dict[str, tuple[int, int]] = {}
    for key, kpi in listed.items():
        lines = [parts[other][2] for other, line in listed.items() if line.parent == kpi.key]
        if lines:
            blocks[kpi.key] = (min(lines), parts[key][2])
    placed: dict[str, str] = {}
    for key in [key for key in kept if key not in listed]:
        index = parts[key][2]
        parent = next((name for name, (first, last) in blocks.items() if min(first, last) <= index <= max(first, last)), None)
        if parent:
            placed[key] = parent
        else:
            del kept[key]
    return placed


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


def row_numbers(table: Table | None, row: int, col: int) -> list[float | None]:
    """A row's own numbers as printed, the read cell first: a repeat the row itself shows is no wrong column, and a
    breakdown's row that gives last year's accepted figure ties out (series_checks.ties_out)."""
    if table is None or row >= len(table.rows):
        return []
    cells = [parse_number(cell) for cell in table.rows[row]]
    return [cells[col].value if col < len(cells) and cells[col] else None,
            *[c.value for i, c in enumerate(cells) if c is not None and i != col][:8]]


def _locator_record(document: Document, locator: Locator) -> dict:
    """What the next quarter's prompt is told about where this KPI was found."""
    if locator.table is not None:
        table = document.tables[locator.table]
        row = table.rows[locator.row] if locator.row < len(table.rows) else []
        return {"table": locator.table, "row": locator.row, "col": locator.col,
                "row_label": _row_label(row, locator.col)[:80], "context": table.context[-80:], "scale": locator.scale,
                # The words the reading quoted for its scale, so a later replay judges the scale exactly as this one did.
                **({"scale_text": clean(locator.scale_text)[:80]} if locator.scale_text else {}),
                "header": column_header(table, locator.row, locator.col),
                # The row's own numbers as printed, the read cell first: a repeat the row itself shows is no wrong column.
                "row_numbers": row_numbers(table, locator.row, locator.col)}
    return {"block": locator.block, "quote": clean(locator.quote or "")[:160], "value_text": locator.value_text}


def locator_hint(locator: dict | None) -> str | None:
    if not locator:
        return None
    if "quote" in locator:
        return f"text like {locator['quote']!r}"
    return f"table row {locator.get('row_label')!r}"


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
