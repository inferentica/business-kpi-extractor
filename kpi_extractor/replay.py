"""Reads a quarter the way the last one was read, with no AI: the same table rows in the same column, or the same
sentence with new numbers. The pipeline accepts a replay only when every check a Flash-only reading must pass holds;
otherwise the AI reads the document as before."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from .ai import Located, Locator, TableLocator
from .document import Document, Table, clean, parse_number
from .extract import PERIOD_TOLERANCE_DAYS, _row_label, column_header
from .xbrl import member_key

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(20\d{2})\b", re.I)
_PERIOD_WORD = (r"\b(?:" + "|".join(_MONTHS) + r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
                r"|first|second|third|fourth|q[1-4])\b")
_TOKEN = re.compile(r"\d[\d.,]*|" + _PERIOD_WORD, re.I)
_YEAR = re.compile(r"\b(20\d{2})\b|(?:\bQ[1-4]|\b[1-4]Q|\bFY)\s*'?(\d{2})\b|'(\d{2})\b", re.I)


def replay(document: Document, last: dict[str, dict], expected: date) -> Located | None:
    """A reading built from last quarter's locators, or None when any of them cannot be found again."""
    tables: dict[str, list[tuple[str, dict]]] = {}
    values: list[Locator] = []
    for key, locator in last.items():
        if locator.get("whole_table"):
            tables.setdefault(key.split(".", 1)[0], []).append((key, locator))
            continue
        found = _replay_value(document, key, locator)
        if found is None:
            return None
        values.append(found)
    table_locators = []
    for group, rows in tables.items():
        found = _replay_table(document, group, [locator for _key, locator in rows])
        if found is None:
            return None
        table_locators.append(found)
    return Located(period_end=reported_period(document, expected).isoformat(), tables=table_locators, values=values)


def reported_period(document: Document, expected: date) -> date:
    """The quarter-end date the document states most often near the nominal one (Apple's June 27 for a June quarter)."""
    counts: Counter[date] = Counter()
    for block in list(document.blocks.values())[:40]:
        for month, day, year in _DATE.findall(block.text):
            try:
                found = date(int(year), _MONTHS.index(month.lower()) + 1, int(day))
            except ValueError:
                continue
            if abs((found - expected).days) <= PERIOD_TOLERANCE_DAYS:
                counts[found] += 1
    return counts.most_common(1)[0][0] if counts else expected


def _replay_value(document: Document, key: str, locator: dict) -> Locator | None:
    if "quote" in locator:
        return _replay_quote(document, key, locator)
    label = clean(locator.get("row_label") or "").lower()
    col = locator.get("col")
    if not label or col is None:
        return None
    matches = []
    for table in _tables_by_preference(document, locator.get("table")):
        rows = [index for index, row in enumerate(table.rows)
                if clean(_row_label(row, col)).lower() == label and parse_number(table.cell(index, col))]
        if len(rows) == 1 and current_column(table, rows[0], col) and _same_header(table, rows[0], col, locator):
            matches.append((table.id, rows[0]))
    if not matches or (len(matches) > 1 and matches[0][0] != locator.get("table")):
        return None  # the row is gone, or several tables carry it and none is last quarter's
    table_id, row = matches[0]
    return Locator(kpi=key, table=table_id, row=row, col=col, scale=locator.get("scale"))


def _replay_table(document: Document, group: str, parts: list[dict]) -> TableLocator | None:
    first = parts[0]
    col = first.get("col")
    if col is None or "total_row_label" not in first:
        return None  # read before replays recorded the total row
    wanted = {member_key(part["row_label"]) for part in parts}
    total_label = first["total_row_label"]
    for table in _tables_by_preference(document, first.get("table")):
        rows: dict[str, int] = {}
        for index, row in enumerate(table.rows):
            if parse_number(table.cell(index, col)) is not None:
                rows.setdefault(member_key(_row_label(row, col)), index)
        total_row = rows.get(member_key(total_label)) if total_label else None
        if not wanted <= rows.keys() or (total_label and total_row is None):
            continue
        indexes = [rows[key] for key in wanted]
        first_row, last_row = min(indexes), max(indexes)
        if total_row is not None and total_row > last_row:
            last_row = total_row - 1  # rows added since last quarter (a new node) sit above the total
        if total_row is not None and first_row <= total_row <= last_row:
            continue
        if not current_column(table, first_row, col) or not _same_header(table, first_row, col, first):
            continue
        return TableLocator(group=group, table=table.id, col=col, first_row=first_row, last_row=last_row,
                            total_row=total_row, scale=first.get("scale"))
    return None


def _replay_quote(document: Document, key: str, locator: dict) -> Locator | None:
    quote, value_text = clean(locator.get("quote") or ""), clean(locator.get("value_text") or "")
    start = quote.find(value_text)
    if not quote or start < 0:
        return None
    tokens = list(_TOKEN.finditer(quote))
    numbers = [token for token in tokens if token.group()[0].isdigit()]
    # The number the value was read from: the first one inside value_text.
    index = next((i for i, token in enumerate(numbers) if start <= token.start() < start + len(value_text)), None)
    if index is None:
        return None
    prefix = value_text[:numbers[index].start() - start]
    suffix = value_text[numbers[index].end() - start:]
    found = []
    # Strict first (only the value and period words change, so "3-nanometer" stays 3), then every number may change.
    for loose in (False, True):
        pattern, position, group, value_group = [], 0, 0, None
        for token in tokens:
            pattern.append(re.escape(quote[position:token.start()]))
            if token is numbers[index] or (loose and token.group()[0].isdigit() and not _names(quote, token)):
                group += 1
                value_group = group if token is numbers[index] else value_group
                pattern.append(r"(\d[\d.,]*)")
            elif token.group()[0].isdigit():
                pattern.append(re.escape(token.group()))
            else:
                pattern.append(r"[A-Za-z0-9]+")
            position = token.end()
        pattern.append(re.escape(quote[position:]))
        compiled = re.compile("".join(pattern), re.I)
        found = [(block.id, match) for block in document.blocks.values() for match in compiled.finditer(block.text)]
        if found:
            break
    if len(found) != 1:
        return None  # gone, or more than one sentence of that shape (this year's and last year's)
    block_id, match = found[0]
    new_value = prefix + match.group(value_group) + suffix
    if new_value not in match.group(0):
        return None
    return Locator(kpi=key, block=block_id, quote=match.group(0), value_text=new_value)


def _names(text: str, token: re.Match) -> bool:
    """Whether a number is part of a name rather than a figure: "3-nanometer", "5G", "M365" keep their number."""
    before, after = text[max(0, token.start() - 1):token.start()], text[token.end():token.end() + 2]
    return bool(re.match(r"-?[A-Za-z]", after)) or bool(re.match(r"[A-Za-z]", before))


def _same_header(table: Table, first_data_row: int, col: int, locator: dict) -> bool:
    """The column is headed as last quarter's was (three months, not six; this year's quarter, not a year to date)."""
    return "header" not in locator or column_header(table, first_data_row, col) == locator["header"]


def _tables_by_preference(document: Document, table_id: str | None) -> list[Table]:
    tables = list(document.tables.values())
    return sorted(tables, key=lambda table: table.id != table_id)


def current_column(table: Table, first_data_row: int, col: int) -> bool:
    """Whether a column is the newest period the table shows: when its header names years, it names the latest."""
    def years(texts: list[str]) -> set[int]:
        found = set()
        for text in texts:
            for four, short, apostrophe in _YEAR.findall(text):
                found.add(int(four) if four else 2000 + int(short or apostrophe))
        return found

    header_rows = table.rows[:max(first_data_row, 1)][:6]
    column = years([row[col] for row in header_rows if col < len(row)])
    every = years([cell for row in header_rows for cell in row[1:]])
    if not every:
        return True  # no years in the header: the prior-period checks decide
    return bool(column) and max(column) == max(every)
