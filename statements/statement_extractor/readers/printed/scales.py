"""The scales a printed statement's figures are in: its money ("in millions"), and its share counts, which a filer
can print in another scale ("in millions, except share amounts which are reflected in thousands") or name per line
("Basic weighted average shares outstanding (thousands)")."""
from __future__ import annotations

import re

from .tables import Table

_UNIT = r"(thousands?|millions?|billions?)"  # TSMC's 2003 quarterly reports: "In Thousand New Taiwan Dollars"
_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_MONEY = re.compile(r"\b(?:in|amounts in|dollars in|expressed in)\s+(?:[A-Z]{2,3}\$?\s+|US\$\s*|NT\$\s*|€\s*|euros?\s+)?"
                    + _UNIT + r"\b", re.I)
# Words that put a scale on share counts rather than money.
_SHARES_IN = re.compile(r"\bshares?(?:\s+(?:amounts?|data|counts?|outstanding))?(?:\s*,)?(?:\s+which\s+are)?"
                        r"(?:\s+(?:reflected|stated|presented|expressed))?\s+(?:are\s+)?in\s+" + _UNIT + r"\b|"
                        r"\b" + _UNIT + r"\s+of\s+shares\b", re.I)
# "except share and per share amounts", "except share data", "except number of shares": share counts in units.
_SHARES_EXCEPTED = re.compile(r"\bexcept\b[^)]*?\b(?<!per )(share|shares)\b(?!\s+(?:amounts?\s+)?which)", re.I)
_LINE_UNIT = re.compile(r"\(\s*(?:in\s+)?" + _UNIT + r"(?:\s+of\s+shares)?\s*\)|\bin\s+" + _UNIT + r"\s*\)?\s*:?$", re.I)


def scale_text(group: list[Table]) -> str:
    first = group[0]
    leading = []
    for row in first.rows:
        if row.figures:
            break
        leading.append(row.label)  # "(In millions except par value)" printed as a row above the figures
    return first.context[-400:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in first.header) + " " + \
        " ".join(leading)


def statement_scale(group: list[Table]) -> float | None:
    """The money scale, when the statement states one; a scale stated for its share counts is not one."""
    text = _SHARES_IN.sub(" ", scale_text(group))
    found = {_SCALES[match.group(1).lower()] for match in _MONEY.finditer(text)}
    return found.pop() if len(found) == 1 else None


def share_scale(group: list[Table], money: float) -> float:
    """The scale of the statement's share counts: the one stated for them, else none when the money scale excepts
    them, else the money scale (Coca-Cola's "in millions except per share data" prints shares in millions)."""
    text = scale_text(group)
    stated = _SHARES_IN.search(text)
    if stated:
        return _SCALES[next(group for group in stated.groups() if group).lower()]
    if _SHARES_EXCEPTED.search(text):
        return 1.0
    return money


def line_scale(*texts: str) -> float | None:
    """A scale printed in a share line's own label or heading: "(thousands)", "(in millions)"."""
    for text in texts:
        match = _LINE_UNIT.search(text or "")
        if match:
            return _SCALES[next(group for group in match.groups() if group).lower()]
    return None
