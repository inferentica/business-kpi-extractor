"""The scales a printed statement's figures are in: its money ("in millions"), and its share counts, which a filer
can print in another scale ("in millions, except share amounts which are reflected in thousands") or name per line
("Basic weighted average shares outstanding (thousands)")."""
from __future__ import annotations

import re

from .tables import Table

_UNIT = r"(thousands?|millions?|billions?)"  # TSMC's 2003 quarterly reports: "In Thousand New Taiwan Dollars"
_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_MONEY = re.compile(r"\b(?:in|amounts in|dollars in|expressed in|figures(?:\s+in)?)\s+(?:[A-Z]{2,3}\$?\s+|US\$\s*|NT\$\s*|€\s*|euros?\s+)?"
                    + _UNIT + r"\b", re.I)
# A currency sign before the unit: Scotiabank's "($ millions)", "US$ millions", Lloyds's "£ million" and "£m".
_DOLLAR_UNIT = re.compile(r"(?:(?:US|C|CDN|NT|HK)?\$|£|€|¥)\s*" + _UNIT + r"\b|"
                          r"(?<![\w.])£m\b|"
                          # James Hardie's "(Millions of US dollars)"
                          r"\b" + _UNIT + r"\s+of\s+(?:[A-Z.]+\s+|new\s+taiwan\s+|hong\s+kong\s+)?(?:dollars|euros?|pounds|yen|won|reais|francs|yuan|renminbi)\b|"
                          r"\(\s*" + _UNIT + r"\s*(?:,\s*except\b[^)]*)?\)", re.I)  # Brookfield's "(MILLIONS)"
# Thousands written as zeros: Ross's "($000, except stores and per share data, unaudited)", Icahn's "(In $000s, except
# unit amounts)" and "(In 000s ...)", "($000's)", "(000 omitted)".
_ZEROS = re.compile(r"\(\s*(?:in\s+)?\$?\s*000(?:'?s)?(?:\s+omitted)?\s*[,)]|\bin\s+\$?\s*000'?s\b", re.I)
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


# Money and share counts in one scale: Laureate's "(Dollar and share amounts in thousands, except per share data)".
_BOTH = re.compile(r"\bdollars?\s+and\s+share\s+amounts\s+(?:are\s+)?in\s+" + _UNIT + r"\b", re.I)
_CURRENCY_WORDS = re.compile(r"\b(won|yen|euros?|pounds|reais|francs|yuan|renminbi|rmb|nt\$|u\.?s\.?\s*dollars|"
                             r"new\s+taiwan\s+dollars)\b", re.I)


def _scale(match: re.Match) -> float:
    unit = next((unit for unit in match.groups() if unit), None)
    return _SCALES[unit.lower()] if unit else 1e6  # "£m"


def statement_scale(group: list[Table]) -> float | None:
    """The money scale, when the statement states one; a scale stated for its share counts is not one. A statement
    with a convenience translation states one scale for each currency (KEPCO: "(In millions...)" over its won columns,
    "(In thousands...)" over the U.S. dollar ones): the first, over the reporting currency's columns, is the money
    scale; the translated columns are never read."""
    raw = scale_text(group)
    both = [_SCALES[match.group(1).lower()] for match in _BOTH.finditer(raw)]
    text = _SHARES_IN.sub(" ", raw)
    matches = sorted((match for pattern in (_MONEY, _DOLLAR_UNIT) for match in pattern.finditer(text)),
                     key=lambda match: match.start())
    found = {_scale(match) for match in matches} | set(both)
    if _ZEROS.search(text):
        found.add(1e3)
    if len(found) == 2 and matches and len({word.lower() for word in _CURRENCY_WORDS.findall(raw)}) >= 2:
        return _scale(matches[0])
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
        short = re.search(r"\(\s*'?\s*(M|K|000)\s*\)", text or "")  # TSMC's "Weighted Average Outstanding Shares ('M)",
        if short:                                                       # its 1999-2000 releases' "Shares Outstanding ('K)"
            return 1e6 if short.group(1) == "M" else 1e3
        match = _LINE_UNIT.search(text or "")
        if match:
            return _SCALES[next(group for group in match.groups() if group).lower()]
    return None
