"""A statement of comprehensive income that is also the income statement: its net income comes below lines of its
own. Brookfield Asset Management Ltd.'s, untaxed, reaches its net income from its share of the business it holds a
quarter of, with no profit before tax to name it (readers/printed/find.py); a statement of other comprehensive income
alone opens with its net income."""
from __future__ import annotations

import re

_NET_PROFIT = re.compile(r"^\(?net\s+(income|profit|earnings|loss|\(loss\))\b", re.I)


_PROFIT_WORDS = re.compile(r"\b(profit|income|loss|earnings)\b", re.I)
_UNDER = re.compile(r"\bunder\b|\bas\s+(?:reported|issued)\b", re.I)


def profit_below_lines(table) -> bool:
    """A net income line after three lines with figures of its own, the first of them no profit line: a note
    reconciling two standards opens with a profit and reaches one "under" the other (POSCO's IFRS and K-IFRS)."""
    lines = [row for row in table.rows if row.figures]
    if not lines or _PROFIT_WORDS.search(lines[0].label):
        return False
    for above, row in enumerate(lines):
        if _NET_PROFIT.match(row.label):
            return above >= 3 and not _UNDER.search(row.label)
    return False


def income_in_one(pages: list, pretax, profit_line) -> bool:
    """Profit and other comprehensive income in one statement: a profit before tax line on its pages (TSMC's
    Taiwan-IFRS reports, KEPCO's continued pages), or a net income below lines of its own."""
    return any(row.figures and pretax.search(row.label) and profit_line.match(row.label)
               for page in pages for row in page.rows) or profit_below_lines(pages[0])
