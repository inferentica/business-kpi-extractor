"""Reads a quarter the way the last one was read, with no AI: the same table rows in the same column, or the same
sentence with new numbers. The pipeline accepts a replay only when every check a Flash-only reading must pass holds;
otherwise the AI reads the document as before."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from ..ai import Located, Locator, TableLocator
from ..documents.document import Document, Table, clean, parse_number
from ..quality.checks import PERIOD_TOLERANCE_DAYS
from .anchor import anchor_words
from .extract import _row_label, column_header
from ..xbrl import member_key

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(20\d{2})\b", re.I)
_PERIOD_WORD = (r"\b(?:" + "|".join(_MONTHS) + r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
                r"|first|second|third|fourth|q[1-4]|[1-4]q)\b")
# Period words first, so a quarter's label ("3Q'22") is one period word, not a number glued to a letter.
_TOKEN = re.compile(_PERIOD_WORD + r"|\d[\d.,]*", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b|(?:\bQ[1-4]|\b[1-4]Q|\bFY)\s*'?(\d{2})\b|'(\d{2})\b", re.I)

_PERIOD_WORDS = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                           r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|first|second|third|fourth|q[1-4])\b")


def _template(text: str) -> str:
    """A quote with its numbers and period words blanked: "DAP was 3.60 billion on average for June 2026" and the
    same sentence for September share one template."""
    return re.sub(r"[\d.,]+", "#", _PERIOD_WORDS.sub("@", clean(text).lower()))


def _same_place(now: dict, before: dict) -> bool:
    """Whether a KPI was found where it was last quarter: the same table row label, or the same sentence shape; for a
    learned rule's reading, the same words before the figure (readers/anchor.py)."""
    if now.get("anchor") and "quote" in before:
        words = tuple(str(now["anchor"]).split())
        return anchor_words(before["quote"], before.get("value_text") or "", len(words)) == words
    if "quote" in now and "quote" in before:
        return _template(now["quote"]) == _template(before["quote"])
    if "row_label" in now and "row_label" in before:
        return clean(now["row_label"]).lower() == clean(before["row_label"]).lower() and bool(now["row_label"])
    return False


def _source_template(url: str) -> str:
    """A document's kind from its file name with the dates and numbers taken out ("a2q26e_withguidance.htm")."""
    return re.sub(r"\d+", "", url.rsplit("/", 1)[-1].lower())



def asked_of(last: dict[str, dict], spec) -> dict[str, dict]:
    """Last quarter's locators for what a reading was asked: its groups' listed KPIs, and its whole tables' rows. A group
    code replayed, and a KPI an older list had, were never asked of it, so they are not missing from it."""
    groups = {group.key for group in spec.groups}
    listed = {f"{group.key}.{kpi.key}" for group in spec.groups for kpi in group.kpis}
    return {key: locator for key, locator in last.items()
            if key in listed or (locator.get("whole_table") and key.split(".", 1)[0] in groups)}


def replay(document: Document, last: dict[str, dict], expected: date, quarter: int | None = None) -> Located | str:
    """A reading built from last quarter's locators, or, when any of them cannot be found again, why not (counted in the
    run summary, so the reasons code most often fails on show where it should learn next). `quarter` is the filing's
    fiscal quarter: a column headed with a quarter's name must name it."""
    tables: dict[str, list[tuple[str, dict]]] = {}
    values: list[Locator] = []
    for key, locator in last.items():
        if locator.get("whole_table"):
            tables.setdefault(key.split(".", 1)[0], []).append((key, locator))
            continue
        found = _replay_value(document, key, locator, quarter)
        if isinstance(found, str):
            return found
        values.append(found)
    table_locators = []
    for group, rows in tables.items():
        found = _replay_table(document, group, [locator for _key, locator in rows], quarter)
        if isinstance(found, str):
            return found
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


def _replay_value(document: Document, key: str, locator: dict, quarter: int | None = None) -> Locator | str:
    if "quote" in locator:
        return _replay_quote(document, key, locator)
    label = clean(locator.get("row_label") or "").lower()
    col = locator.get("col")
    if not label or col is None:
        return "row not recorded"
    matches, misses = [], []  # misses: why each table that has the label does not give the value, last quarter's first
    for table in _tables_by_preference(document, locator.get("table")):
        rows = [index for index, row in enumerate(table.rows)
                if clean(_row_label(row, col)).lower() == label and parse_number(table.cell(index, col))]
        if len(rows) > 1:
            misses.append("row label found more than once")
        elif rows and not current_column(table, rows[0], col):
            misses.append("column not the newest period")
        elif rows and not _same_header(table, rows[0], col, locator, quarter):
            misses.append("column header changed" if "header" in locator else "column header not recorded")
        elif rows:
            matches.append((table.id, rows[0]))
    if not matches:
        return misses[0] if misses else "row label not found"
    if len(matches) > 1 and matches[0][0] != locator.get("table"):
        return "row in several tables"  # and none of them is last quarter's
    table_id, row = matches[0]
    return Locator(kpi=key, table=table_id, row=row, col=col, scale=locator.get("scale"))


def _replay_table(document: Document, group: str, parts: list[dict], quarter: int | None = None) -> TableLocator | str:
    first = parts[0]
    col = first.get("col")
    if col is None or "total_row_label" not in first:
        return "total row not recorded"  # read before replays recorded it
    misses = []  # why each table that has the rows does not give them, last quarter's first
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
            misses.append("total row inside the table's rows")
            continue
        if not current_column(table, first_row, col) or not _same_header(table, first_row, col, first, quarter):
            misses.append("column header changed" if "header" in first else "column header not recorded")
            continue
        return TableLocator(group=group, table=table.id, col=col, first_row=first_row, last_row=last_row,
                            total_row=total_row, scale=first.get("scale"))
    return misses[0] if misses else "table rows not found"


def _replay_quote(document: Document, key: str, locator: dict) -> Locator | str:
    quote, value_text = clean(locator.get("quote") or ""), clean(locator.get("value_text") or "")
    start = quote.find(value_text)
    if not quote or start < 0:
        return "quote not recorded"
    if quote.count(value_text) > 1:
        return "value repeats in the sentence"  # "Equity 189 192 192": which 192 was read is not recorded
    tokens = list(_TOKEN.finditer(quote))
    numbers = [token for token in tokens if _is_number(token)]
    # The number the value was read from: the first one inside value_text.
    index = next((i for i, token in enumerate(numbers) if start <= token.start() < start + len(value_text)), None)
    if index is None:
        return "quote not recorded"
    prefix = value_text[:numbers[index].start() - start]
    suffix = value_text[numbers[index].end() - start:]
    found = []
    # Strict first (only the value and period words change, so "3-nanometer" stays 3), then every number may change.
    for loose in (False, True):
        pattern, position, group, value_group = [], 0, 0, None
        for token in tokens:
            pattern.append(re.escape(quote[position:token.start()]))
            if token is numbers[index] or (loose and _is_number(token) and not _names(quote, token)):
                group += 1
                value_group = group if token is numbers[index] else value_group
                pattern.append(r"(\d[\d.,]*)")
            elif _is_number(token):
                pattern.append(re.escape(token.group()))
            else:
                pattern.append(r"[A-Za-z0-9]+")
            position = token.end()
        pattern.append(re.escape(quote[position:]))
        compiled = re.compile("".join(pattern), re.I)
        found = [(block.id, match) for block in document.blocks.values() for match in compiled.finditer(block.text)]
        if found:
            break
    if len(found) != 1:  # gone, or more than one sentence of that shape (this year's and last year's)
        return "sentence found more than once" if found else "sentence not found"
    block_id, match = found[0]
    new_value = prefix + match.group(value_group) + suffix
    if new_value not in match.group(0):
        return "sentence not found"
    return Locator(kpi=key, block=block_id, quote=match.group(0), value_text=new_value)


# What makes a number part of a name: a letter glued after it ("5G", "3nm") but not a scale ("$97bn"), a hyphened word
# ("3-nanometer"), or a process node's unit ("3 nm"). A figure followed by a word ("2019, an increase", "$435 million")
# is a figure: it changes with the quarter.
_NAMING_AFTER = re.compile(r"(?!(?:bn|mn|bps)\b)[A-Za-z]|-[A-Za-z]|\s(?:nm|nanomet(?:er|re)s?)\b", re.I)


def _is_number(token: re.Match) -> bool:
    return token.group()[0].isdigit() and not token.group()[-1].isalpha()  # "3Q" is a period word


def _names(text: str, token: re.Match) -> bool:
    """Whether a number is part of a name rather than a figure: "3-nanometer", "3 nm", "5G", "M365" keep their number."""
    before = text[max(0, token.start() - 1):token.start()]
    glued_after = token.group()[-1].isdigit() and _NAMING_AFTER.match(text, token.end())
    return bool(glued_after) or bool(re.match(r"[A-Za-z]", before))


def _same_header(table: Table, first_data_row: int, col: int, locator: dict, quarter: int | None = None) -> bool:
    """The column is headed as last quarter's was (three months, not six; this year's quarter, not a year to date). The
    quarter's own name and the date's form may differ ("Q2" for "Q1", "June 2026" for "March 31, 2026"), but a column
    that names its quarter must name this filing's: never a neighbouring quarter's column read as this one."""
    # A reading stored before headers were recorded carries no evidence of its column: the AI reads it once more.
    if "header" not in locator:
        return False
    now = column_header(table, first_data_row, col)
    named = {_QUARTERS[match.group(1)] for match in _QUARTER_NAME.finditer(now)}
    if named and quarter and named != {quarter}:
        return False
    return comparable_header(now) == comparable_header(locator["header"])


_QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_QUARTER_NAME = re.compile(r"<?\b(first|second|third|fourth) quarter\b>?")
_FULL_MONTH = ("january|february|march|april|june|july|august|september|october|november|december")


def comparable_header(header: str) -> str:
    """A recorded column header with what does not change the period taken out: which quarter it names, whether its
    date shows the day, and words a document ran together ("endedseptember", "monthsended"). Headers recorded before
    quarter names were kept ("#q#") compare too."""
    text = re.sub(rf"(?<=[a-z])(?=(?:{_FULL_MONTH})\b)|(?<=months)(?=ended)", " ", header)
    text = re.sub(rf"\b(?:{_FULL_MONTH})\b", "@", text)
    text = _QUARTER_NAME.sub("<quarter>", re.sub(r"(?<![\w<])#q#(?!\w)", "<quarter>", text))
    return re.sub(r"#(?:\s*#)+", "#", text)


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
