"""A statement of comprehensive income that is also the income statement: its net income comes below lines of its
own. Brookfield Asset Management Ltd.'s, untaxed, reaches its net income from its share of the business it holds a
quarter of, with no profit before tax to name it (readers/printed/find.py); a statement of other comprehensive income
alone opens with its net income."""
from __future__ import annotations

import re

_NET_PROFIT = re.compile(r"^\(?net\s+(income|profit|earnings|loss|\(loss\))\b", re.I)


def profit_below_lines(table) -> bool:
    """A net income line after three lines with figures of its own."""
    above = 0
    for row in table.rows:
        if row.figures and _NET_PROFIT.match(row.label):
            return above >= 3
        above += bool(row.figures)
    return False


def income_in_one(pages: list, pretax, profit_line) -> bool:
    """Profit and other comprehensive income in one statement: a profit before tax line on its pages (TSMC's
    Taiwan-IFRS reports, KEPCO's continued pages), or a net income below lines of its own."""
    return any(row.figures and pretax.search(row.label) and profit_line.match(row.label)
               for page in pages for row in page.rows) or profit_below_lines(pages[0])
