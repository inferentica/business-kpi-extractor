"""Scale rules: the words and marks that give a figure's scale (a table's "(in millions)", a row's "(000s)", a
column's "€m"), and the KPIs whose amounts are per unit and keep no table scale."""

from __future__ import annotations

import re

from ..document import clean, Document, Table
from .table import _heading


# A row label's own scale: "(in millions)", "(millions)", or within a longer note ("Starlink Subscribers (#, in millions,
# as of period end)", SpaceX).
_ROW_SCALE = re.compile(r"\((?:in\s+)?(?P<word>thousands|millions|billions)\)|\([^()]*\bin\s+(?P<within>thousands|millions|billions)\b[^()]*\)", re.I)


# A row priced per unit ("Starlink consumer ARPU ($ per month)") in a table "in millions (except where noted)" is in units.
_PER_UNIT = re.compile(r"\([^()]*\bper\s+(?:month|year|quarter|day|user|subscriber|member|customer|share|unit|system)\b[^()]*\)", re.I)


def _preceding_scale(text: str, at: int) -> float | None:
    """The scale of the last heading like "($ in billions)" before position `at` of a text."""
    # "($ in millions, except per share amounts)" is a scale heading too: a price per unit names no millions.
    headings = list(_ROW_SCALE.finditer(text[:max(at, 0)])) + list(_BARE_SCALE.finditer(text[:max(at, 0)]))
    if not headings:
        return None
    last = max(headings, key=lambda match: match.start())
    groups = last.groupdict()
    return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[(groups.get("word") or groups.get("within") or groups.get("bare")).lower()]


# A scale stated without brackets, in the words of a passage: Grifols' "In millions of euros" over its breakdown.
_BARE_SCALE = re.compile(r"\b(?:in|amounts in)\s+(?P<bare>thousands|millions|billions)\s+of\s+(?:euros?|dollars?|US\$|\w+ dollars)", re.I)


# A row label's short scale, Tesla's "Active FSD Subscriptions (mil)". Never a single letter: "(b)" is a footnote.
_ROW_SCALE_SHORT = re.compile(r"\(\s*(?P<word>bil|bn|mil|mm|mn|thous)\.?\s*\)", re.I)


# A KPI priced per unit is in units, whatever its table's "$ Millions, Except as Indicated" or "In thousands, except
# yield amounts" says: FedEx's "Revenue per Package" and "Package Yield", ConocoPhillips' "Realized Price ($/MCF)". Apollo's
# "Yield" is an asset class, so a yield counts only for packages, freight, pounds, pieces, shipments, passengers or units.
_PER_UNIT_KPI = re.compile(r"\bper\s+(?!cent\b)\w|\$\s*/\s*\w|\bprices?\b|\b(?:package|freight|pound|piece|shipment|passenger|unit|composite)\s+(?:freight\s+|package\s+)?yields?\b|\bARP[AU]\b|\bASP\b"
                           r"|\b(?:day|daily)\s?rates?\s*(?:\(|$)|\bADR\b", re.I)  # Borr's "Average Dayrate", never its "Dayrate revenue"


def _row_scale(text: str) -> float | None:
    if _PER_UNIT.search(text):
        return 1.0
    # A label naming two measures ("Leases renewed Kilowatt / NRSF (in thousands)"): the scale after the slash is the
    # second's, so the row states none for the first. Digital Realty's 92,670 kilowatts are not 92.7 million.
    if (slash := re.search(r"\w\s+/\s+\w", text)) and (scaled := re.search(r"\(([^()]*\b(?:thousand|million|billion)s?\b[^()]*)\)", text, re.I)) \
            and scaled.start() > slash.start():
        return None
    if match := _ROW_SCALE.search(text):
        return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[(match.group("word") or match.group("within")).lower()]
    if match := _ROW_SCALE_SHORT.search(text):
        return {"bil": 1e9, "bn": 1e9, "mil": 1e6, "mm": 1e6, "mn": 1e6, "thous": 1e3}[match.group("word").lower()]
    # Auna's unit column "# (000)", a row's own "(Mil.)": the column-heading forms, in the row.
    if (short := _COLUMN_SCALE.search(text)) and (found := _scale_word(short.group("word") or short.group("letter") or short.group("bare"))):
        return found
    return None


# A figure whose scale only the AI gives: held for review, never served on the AI's word.
SCALE_NOT_STATED = "scale not stated in the document"


# A column's own scale: Mastercard's "(Bil.)" and "(Mil.)", Axia's "(R$ mn)". A single letter counts only beside a
# currency sign ("($B)"): a bare "(b)" is a footnote mark, as in Axia's "Regulatory Revenue (c) = (a) x (b)".
# The currency code before it is upper case only ("(R$ mn)", "(US$ bn)"): the pattern ignores case, and "in" read as a code
# made a section row's "(in thousands)" the scale of MP Materials' production volumes in the same column.
_COLUMN_SCALE = re.compile(r"\(\s*(?:(?-i:[A-Z]{1,3}))?\$?\s*(?P<word>bil|bn|billions?|mil|mm|mn|millions?|thous|thousands?|000s?)\.?\s*\)"
                           r"|\(\s*(?:(?-i:[A-Z]{1,3}))?\$\s*(?P<letter>[bmk])\.?\s*\)"
                           r"|(?<![\w.])(?:€|\$|£|¥)\s*(?P<bare>bn|mn|mm|m|k)\b", re.I)  # Vodafone's "€m" column heading


_SCALE_OF = (("bil", 1e9), ("bn", 1e9), ("b", 1e9), ("mil", 1e6), ("mm", 1e6), ("mn", 1e6), ("m", 1e6), ("thous", 1e3), ("k", 1e3),
             ("000", 1e3))


def _scale_word(word: str | None) -> float | None:
    if not word:
        return None
    word = word.lower()
    return next((scale for prefix, scale in _SCALE_OF if word.startswith(prefix)), None)


def _column_scale(table: Table, row: int, col: int) -> float | None:
    """The scale a column's own header states: Mastercard's "GDV (Bil.)" and "Purchase Trans. (Mil.)"."""
    cells = [_heading(header, col) for header in table.rows[:max(row, 1)][:6] if col < len(header)]
    found = {_scale_word(match.group("word") or match.group("letter") or match.group("bare")) for cell in cells for match in _COLUMN_SCALE.finditer(cell)} - {None}
    return found.pop() if len(found) == 1 else None


def _scale_quoted(document: Document, part, scale_text: str | None, scale: float) -> bool:
    """Whether the words a reader quotes for a scale are in the table (its caption, header or rows) or the passages
    just before it, and state that scale."""
    words = clean(scale_text or "")
    if not words:
        return False
    stated = {_SCALE_WORDS_OF[match.group("word").lower().rstrip("s")] for match in _ANY_SCALE.finditer(words)}
    stated |= {_scale_word(match.group("word") or match.group("letter") or match.group("bare")) for match in _COLUMN_SCALE.finditer(words)} - {None}
    if stated != {scale}:
        return False
    if isinstance(part, Table):
        nearby = [part.context, *(" ".join(row) for row in part.rows)]
    else:
        nearby = [part.text]
    key = getattr(part, "id", None)
    if key in document.order:
        at = document.order.index(key)
        nearby += [document.blocks[other].text for other in document.order[max(0, at - 4):at] if other in document.blocks]
    flat = lambda text: re.sub(r"\s+", " ", clean(text)).lower()
    return any(flat(words) in flat(text) for text in nearby)


_ANY_SCALE = re.compile(r"\b(?P<word>thousands?|millions?|billions?)\b", re.I)


_SCALE_WORDS_OF = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def _ai_scale_notes(document: Document, part, scale_text: str | None, scale: float, value: float) -> list[str]:
    """A scale code did not find: one the reader quotes from the document stands, anything else is held for review.
    Scale 1 claims no multiplier (a price per unit, a figure written out in full) and stands as read."""
    if scale == 1:
        return []
    if _scale_quoted(document, part, scale_text, scale):
        return [f"scale quoted: {clean(scale_text or '')[:40]!r}"]
    return [SCALE_NOT_STATED]
