"""A list's "Other" line whose series is read from the income statement is read from the statement's own Other row.
Wells Fargo's releases give noninterest income twice: the income statement's "Other" and a detail table that breaks it
down, ending in "All other" (life insurance investment income and lease income sit beside it). A reader that took the
detail table's "All other", or its "Other (1)", read a part of the line as the line: 2019 Q3's 1,355 for the
statement's 1,528, 2020 Q1's 211 for 372, and seven quarters more (owner's corrections, 2026-11-19 and 2026-11-20).
The statement's row is the one nearest the series' own row, in the first column of the quarter; nothing changes when the
reading is already on a statement, the series was not read from one, or the statement has no such row."""
from __future__ import annotations

import re
from datetime import date

from ..documents.document import Document, parse_number
from .identity import _label_words, _row_label
from .period import header_contradicts

STATEMENT = re.compile(r"statements? of (?:consolidated )?(?:income|operations|earnings)|income statements?", re.I)
_RESIDUAL = (["other"], ["all", "other"])
_NEAR_ROWS = 6  # the statement's Other among its noninterest lines, not the expense section's Other further down


def residual(label: str | None) -> bool:
    """Whether a label is a list's or a row's "Other" ("All other", "Other (1)")."""
    return _label_words(str(label or "")) in _RESIDUAL


def _title(context: str | None) -> str:
    return (context or "")[-80:]


def statement_other_cell(document: Document, reading: dict, series: dict | None, kpi_label: str,
                         expected: date) -> tuple[str, int, int, str] | None:
    """(table, row, column, label) of the income statement's Other row for a reading of the list's Other line taken
    from another table, when the series' own place is that statement's row; None otherwise."""
    if not series or not residual(kpi_label) or not residual(reading.get("row_label")):
        return None
    table = document.tables.get(str(reading.get("table")))
    if table is None or STATEMENT.search(_title(table.context)) or not STATEMENT.search(str(series.get("context") or "")):
        return None
    name, near = _label_words(str(series.get("row_label") or "")), int(series.get("row") or 0)
    if not residual(series.get("row_label")):
        return None
    own = str(series.get("context") or "").strip().lower()
    statements = sorted((key for key in document.order if key in document.tables and STATEMENT.search(_title(document.tables[key].context))),
                        key=lambda key: _title(document.tables[key].context).strip().lower() != own)  # the series' own title first
    for key in statements:
        rows = document.tables[key].rows
        matches = [index for index, row in enumerate(rows) if _label_words(_row_label(row, 1)) == name and abs(index - near) <= _NEAR_ROWS]
        for index in sorted(matches, key=lambda index: abs(index - near))[:1]:
            for col in range(1, len(rows[index])):
                if parse_number(rows[index][col]) is not None and header_contradicts(document.tables[key], index, col, expected) is None:
                    return key, index, col, _row_label(rows[index], col)
    return None
