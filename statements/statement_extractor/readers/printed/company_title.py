"""The company a printed statement's title names: "GOLD TOE MORETZ HOLDINGS CORP. AND SUBSIDIARIES CONSOLIDATED
BALANCE SHEETS", "Quattor Participações S.A. Consolidated Balance Sheets". The pipeline compares it with the filer's
names (pipeline/printed.py), so an acquired company's statements furnished with a release are never the company's."""
from __future__ import annotations

import re

from .find import TITLES
from .tables import Table

# "GOLD TOE MORETZ HOLDINGS CORP. AND SUBSIDIARIES CONSOLIDATED BALANCE SHEETS": the company a statement's title names.
_SUBSIDIARIES = re.compile(r"\band\s+(?:its\s+)?(?:consolidated\s+)?subsidiar(?:y|ies)\b", re.I)
_ABBREVIATIONS = {"INC.", "CORP.", "CO.", "LTD.", "S.A.", "N.V.", "L.P.", "PLC.", "B.V.", "AG.", "S.A.B."}


def titled_company(kind: str, table: Table) -> str | None:
    """The company named just before a statement's title, as "<name> and subsidiaries" or a name ending in its legal
    form ("Quattor Participações S.A. Consolidated Balance Sheets"); None where the title names none that way, or the
    name is cut off or runs into a page's other words."""
    text = table.context[-600:]
    title = None
    for title in dict(TITLES)[kind].finditer(text):
        pass
    if title is None or title.start() < len(text) - 300:
        return None
    named = None
    for named in _SUBSIDIARIES.finditer(text, 0, title.start()):
        pass
    if named is not None and title.start() - named.end() <= 80:
        before = text[:named.start()].split()
    else:
        before = re.sub(r"(?i)\b(unaudited|condensed|interim)\s*$", "", text[:title.start()].rstrip()).split()
        if not before or before[-1].upper().rstrip(",") not in _LEGAL_FORMS:
            return _after_title(text[title.end():])
    words: list[str] = []
    for position in range(len(before) - 1, max(-1, len(before) - 9), -1):
        word = before[position]
        if not re.match(r"[A-Z&]", word) or _PAGE_WORD.match(word) or re.search(r"['’]s$", word, re.I) or \
                (words and word.endswith(".") and word.upper() not in _ABBREVIATIONS):
            break  # a page number or label, a lower-case word or a sentence's end: the text before the name
        if position == 0 and len(table.context) >= 600:
            return None  # the name runs back to where the parser's 600 characters before the table begin: cut off
        words.insert(0, word)
    return " ".join(words) or None


_LEGAL_FORMS = _ABBREVIATIONS | {"INC", "CORP", "CORPORATION", "LTD", "LIMITED", "PLC", "S.A", "AG", "N.V", "SA", "NV"}
_PAGE_WORD = re.compile(r"^(F|FS|FS-R)-?\d+$|^(contents|index|statements?|page)$", re.I)


# The name printed after the title, before its scale: CEMEX's 6-Ks furnish its listed subsidiaries' results, "Income
# Statement & Balance Sheet Information CEMEX Holdings Philippines, Inc. (Thousands of Philippine Pesos …)", "CEMEX Latam
# Holdings, S.A. and Subsidiaries in thousands of U.S. Dollars".
_SCALE_START = re.compile(r"\(|\b(?:in|expressed\s+in)\s+(?:thousands|millions|billions)\b", re.I)
_STATEMENT_WORDS = {"INFORMATION", "SHEET", "SHEETS", "STATEMENT", "STATEMENTS", "BALANCE", "INCOME", "DATA", "&", "AND",
                    "FINANCIAL", "CONSOLIDATED", "OF", "OPERATIONS", "POSITION", "CASH", "FLOWS", "RESULTS"}


def _after_title(text: str) -> str | None:
    segment = text[:(_SCALE_START.search(text) or re.search(r"$", text)).start()][:120]
    named = None
    for named in _SUBSIDIARIES.finditer(segment):
        pass
    before = (segment[:named.start()] if named else segment).split()
    if not before or not named and before[-1].upper().rstrip(",") not in _LEGAL_FORMS:
        return None
    words: list[str] = []
    for word in reversed(before):
        if not re.match(r"[A-Z]", word) or word.upper().strip(",") in _STATEMENT_WORDS or (
                words and word.endswith(".") and word.upper() not in _ABBREVIATIONS):
            break
        words.insert(0, word)
    return " ".join(words) or None
