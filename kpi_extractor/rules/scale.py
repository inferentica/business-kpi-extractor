"""Scale rules: the words and marks that give a figure's scale (a table's "(in millions)", a row's "(000s)", a
column's "€m"), and the KPIs whose amounts are per unit and keep no table scale. Each reading's scale is decided by one
resolver per kind of reading (a table cell, a passage, a whole table), which returns the factor with the source and the
words that decided it (ScaleFrom), stored with the figure as its locator's "scale_from"."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..documents.document import _AMOUNT_WORDS, _SCALE_WORDS, _TABLE_SCALE, clean, Document, parse_number, Table, TextBlock
from .table import _heading, _nearest_heading


@dataclass(frozen=True)
class ScaleFrom:
    """A figure's scale, where it came from and the words that state it. Sources: "number" (the figure's own "1.2
    billion"), "row", "column", "heading" (the row's nearest heading), "per-unit" (a price or rate keeps its units),
    "passage" (the last scale heading before a quote), "table" or "block" (its caption or header), "document" (the one
    scale it declares throughout), "quoted" (words the reader quotes from the document), "reader" (the reader's scale
    alone, held for review), "units" (a count with no scale anywhere)."""
    factor: float
    source: str
    words: str | None = None

    def record(self) -> dict:
        return {"factor": self.factor, "source": self.source, **({"words": clean(self.words)[:60]} if self.words else {})}


# A row label's own scale: "(in millions)", "(millions)", or within a longer note ("Starlink Subscribers (#, in millions,
# as of period end)", SpaceX). Trillions too: JPMorgan's "Total payments transaction volume (in trillions)" in a table
# "(in millions)" was read as 1.8 million once the row's own word went unseen.
_ROW_SCALE = re.compile(r"\((?:in\s+)?(?P<word>thousands|millions|billions|trillions)\)|\([^()]*\bin\s+(?P<within>thousands|millions|billions|trillions)\b[^()]*\)", re.I)
SCALE_WORDS = {"thousands": 1e3, "millions": 1e6, "billions": 1e9, "trillions": 1e12}


# A row priced per unit ("Starlink consumer ARPU ($ per month)") in a table "in millions (except where noted)" is in units.
# A row stating its amount per unit: "(per share)", "($ per month)", or a price's own unit, Exxon's "Brent ($/b)",
# "Henry Hub ($/mbtu)", "Natural Gas ($/kcf)" (a "($ in millions)" caption names no unit after the slash), or a row naming
# its currency alone: American Express's "Average proprietary basic Card Member spending (dollars)" $6,110 in a table
# "(Millions, except percentages and where indicated)" was read as $6.1 billion ("(dollars in millions)" is a caption).
_PER_UNIT = re.compile(r"\([^()]*\bper\s+(?:month|year|quarter|day|user|subscriber|member|customer|share|unit|system)\b[^()]*\)"
                       r"|\(\s*(?:US)?[$€£¥]\s*/\s*[A-Za-z]+\s*\)|\(\s*(?:in\s+)?(?:U\.?S\.?\s+)?(?:dollars|euros|pounds|yen)\s*\)", re.I)


def _preceding_scale(text: str, at: int) -> float | None:
    """The scale of the last heading like "($ in billions)" before position `at` of a text."""
    return (_preceding_scale_words(text, at) or (None,))[0]


def _preceding_scale_words(text: str, at: int) -> tuple[float, str] | None:
    """The scale of the last heading like "($ in billions)" before position `at` of a text, and the heading's words."""
    # "($ in millions, except per share amounts)" is a scale heading too: a price per unit names no millions.
    headings = list(_ROW_SCALE.finditer(text[:max(at, 0)])) + list(_BARE_SCALE.finditer(text[:max(at, 0)]))
    if not headings:
        return None
    last = max(headings, key=lambda match: match.start())
    groups = last.groupdict()
    return SCALE_WORDS[(groups.get("word") or groups.get("within") or groups.get("bare")).lower()], last.group(0)


# A scale stated without brackets, in the words of a passage: Grifols' "In millions of euros" over its breakdown.
_BARE_SCALE = re.compile(r"\b(?:in|amounts in)\s+(?P<bare>thousands|millions|billions|trillions)\s+of\s+(?:euros?|dollars?|US\$|\w+ dollars)", re.I)


# A row label's short scale, Tesla's "Active FSD Subscriptions (mil)". Never a single letter: "(b)" is a footnote.
_ROW_SCALE_SHORT = re.compile(r"\(\s*(?P<word>bil|bn|mil|mm|mn|thous)\.?\s*\)", re.I)


# A KPI priced per unit is in units, whatever its table's "$ Millions, Except as Indicated" or "In thousands, except
# yield amounts" says: FedEx's "Revenue per Package" and "Package Yield", ConocoPhillips' "Realized Price ($/MCF)". Apollo's
# "Yield" is an asset class, so a yield counts only for packages, freight, pounds, pieces, shipments, passengers or units.
_PER_UNIT_KPI = re.compile(r"\bper\s+(?!cent\b)\w|\$\s*/\s*\w|\bprices?\b|\b(?:package|freight|pound|piece|shipment|passenger|unit|composite)\s+(?:freight\s+|package\s+)?yields?\b|\bARP[AU]\b|\bASP\b"
                           r"|\b(?:day|daily)\s?rates?\s*(?:\(|$)|\bADR\b", re.I)  # Borr's "Average Dayrate", never its "Dayrate revenue"


def _row_scale(text: str) -> float | None:
    return (_row_scale_words(text) or (None,))[0]


def _row_scale_words(text: str) -> tuple[float, str] | None:
    """A row label's own scale and the words stating it."""
    if per_unit := _PER_UNIT.search(text):
        return 1.0, per_unit.group(0)
    # A label naming two measures ("Leases renewed Kilowatt / NRSF (in thousands)"): the scale after the slash is the
    # second's, so the row states none for the first. Digital Realty's 92,670 kilowatts are not 92.7 million.
    if (slash := re.search(r"\w\s+/\s+\w", text)) and (scaled := re.search(r"\(([^()]*\b(?:thousand|million|billion|trillion)s?\b[^()]*)\)", text, re.I)) \
            and scaled.start() > slash.start():
        return None
    if match := _ROW_SCALE.search(text):
        return SCALE_WORDS[(match.group("word") or match.group("within")).lower()], match.group(0)
    if match := _ROW_SCALE_SHORT.search(text):
        return {"bil": 1e9, "bn": 1e9, "mil": 1e6, "mm": 1e6, "mn": 1e6, "thous": 1e3}[match.group("word").lower()], match.group(0)
    # Auna's unit column "# (000)", a row's own "(Mil.)": the column-heading forms, in the row.
    if (short := _COLUMN_SCALE.search(text)) and (found := _scale_word(short.group("word") or short.group("letter") or short.group("bare"))):
        return found, short.group(0)
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
    return (_column_scale_words(table, row, col) or (None,))[0]


def _column_scale_words(table: Table, row: int, col: int) -> tuple[float, str] | None:
    """The one scale a column's own header states, and its words."""
    cells = [_heading(header, col) for header in table.rows[:max(row, 1)][:6] if col < len(header)]
    found: dict[float, str] = {}
    for cell in cells:
        for match in _COLUMN_SCALE.finditer(cell):
            if (scale := _scale_word(match.group("word") or match.group("letter") or match.group("bare"))) is not None:
                found.setdefault(scale, match.group(0))
    return next(iter(found.items())) if len(found) == 1 else None


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


_YEAR = re.compile(r"\s*(?:FY\s*)?(?:19|20)\d{2}\s*")


def _first_figures_row(table: Table) -> int:
    """The first row with a label and a figure: a header's years ("2026") are no figures."""
    for index, cells in enumerate(table.rows):
        if cells and cells[0].strip() and any(parse_number(cell) is not None and not _YEAR.fullmatch(cell)
                                              and not re.search(r"[A-Za-z]", cell) for cell in cells[1:]):
            return index
    return len(table.rows)


def table_scale_words(table: Table) -> tuple[float, str] | None:
    """The multiplier a table's own header or caption declares for its amounts, e.g. "(in millions)", and its words. A
    header that declares one scale for counts and another for amounts ("(Units in thousands, Revenue in millions)",
    Apple's summary data to 2018) gives amounts the scale named for them. The header ends at the first row of figures:
    a heading among the rows ("($ billions)" under "Revenue 4,100") heads only its own rows (_body_scale)."""
    header = " ".join(" ".join(row) for row in table.rows[:min(4, _first_figures_row(table))])
    text = f"{table.context[-160:]} {header}"
    found = [(text[max(0, match.start() - 24):match.start()], _SCALE_WORDS[match.group("word").lower().rstrip("s")], match.group(0).strip())
             for match in _TABLE_SCALE.finditer(text)]
    if len({scale for _, scale, _ in found}) > 1:
        named = [(scale, words) for before, scale, words in found if _AMOUNT_WORDS.search(before)]
        if named:
            return named[-1]
    return (found[0][1], found[0][2]) if found else None


def block_scale_words(block: TextBlock) -> tuple[float, str] | None:
    """The one amount scale a text block declares, e.g. "(Unaudited, €, in millions)" over figures that reach us as
    text (ASML's statements are slide images with their figures as hidden text), and the first words declaring it."""
    found: dict[float, str] = {}
    for match in _TABLE_SCALE.finditer(block.text):
        found.setdefault(_SCALE_WORDS[match.group("word").lower().rstrip("s")], match.group(0).strip())
    return next(iter(found.items())) if len(found) == 1 else None


def document_scale_words(document: Document) -> tuple[float, str] | None:
    """The one amount scale a document declares throughout (TSMC's reports say "in thousands of New Taiwan dollars"
    only in page headings), and the first words declaring it; None when it declares none or several."""
    found: dict[float, str] = {}
    for item in [*document.blocks.values(), *document.tables.values()]:
        text = item.text if isinstance(item, TextBlock) else f"{item.context} {' '.join(' '.join(r) for r in item.rows[:3])}"
        for match in _TABLE_SCALE.finditer(text):
            found.setdefault(_SCALE_WORDS[match.group("word").lower().rstrip("s")], match.group(0).strip())
    return next(iter(found.items())) if len(found) == 1 else None


def number_scale(scale_word: str) -> ScaleFrom:
    """A figure that states its own scale ("$1.2 billion", "23.4 mn")."""
    return ScaleFrom(_SCALE_WORDS.get(scale_word.lower().rstrip("s"), 1.0), "number", scale_word)


def cell_count_scale(document: Document, table: Table, row: int, col: int, locator) -> tuple[ScaleFrom, list[str]]:
    """A count's scale in a table: its own row label ("Paid memberships (in millions)"), its column's ("Purchase Trans.
    (Mil.)"), or its nearest heading's (FedEx's "Avg. daily package volume (ADV) (000s):" heads the rows under it; never
    one further up, as "Revenue: (in thousands)" over MP Materials' tonnages). A table's "(in millions, except employee
    data)" describes its amounts, not its counts. Otherwise a scale the reading quotes from the document, or the
    reader's own, held for review."""
    for source, found in (("row", _row_scale_words(" ".join(table.rows[row]))), ("column", _column_scale_words(table, row, col)),
                          ("heading", _row_scale_words(_nearest_heading(table, row)))):
        if found:
            return ScaleFrom(found[0], source, found[1]), []
    if locator.scale in (1e3, 1e6, 1e9):
        if _scale_quoted(document, table, locator.scale_text, locator.scale):
            return ScaleFrom(locator.scale, "quoted", locator.scale_text), [f"scale quoted: {clean(locator.scale_text or '')[:40]!r}"]
        return ScaleFrom(locator.scale, "reader", locator.scale_text), [SCALE_NOT_STATED]
    return ScaleFrom(1.0, "units"), []


def _heading_scale_words(text: str) -> tuple[float, str] | None:
    """The scale a heading row states ("($ billions)", "(in millions)"): scale words only, never a per-unit reading
    of a caption's "except per share amounts"."""
    if match := _ROW_SCALE.search(text):
        return SCALE_WORDS[(match.group("word") or match.group("within")).lower()], match.group(0)
    if (short := _COLUMN_SCALE.search(text)) and (found := _scale_word(short.group("word") or short.group("letter") or short.group("bare"))):
        return found, short.group(0)
    return None


def _body_scale(table: Table, row: int, col: int) -> tuple[tuple[float, str] | None, tuple[float, str] | None]:
    """The scale the nearest heading inside the table above a row states: a row with no figures, below the table's
    first row of figures, in its label ("($ billions)") or repeated as a header in the figure's own column (ServiceNow's
    "Amount ($ billions)" over cRPO and RPO, under "Amount ($ millions)" at the top). It holds for every row under it
    until the next heading; never the table's own header, whose cells head columns with scales of their own. A heading
    that states no scale ("Non-GAAP results:") starts rows of its own: a scale heading above it reaches no further, and
    comes back second, as one that may or may not head the row."""
    def figures(cells: list[str]) -> bool:  # a header's footnote mark, "Amount ($ billions)(2)", is no figure
        return any(parse_number(cell) and not re.search(r"[A-Za-z]{3}", cell) for cell in cells[1:])

    first = next((i for i, cells in enumerate(table.rows) if figures(cells)), len(table.rows))
    section = False
    for index in range(row - 1, first, -1):
        cells = table.rows[index]
        if figures(cells):
            continue
        if found := _heading_scale_words(f"{cells[0] if cells else ''} {_heading(cells, col) if col < len(cells) else ''}"):
            return (None, found) if section else (found, None)
        if cells and cells[0].strip():
            section = True
    return None, None


SCALE_HEADING_UNSURE = "a scale heading further up the table may head this row"


def _own_amount_scale(table: Table, row: int, col: int, kpi_label: str) -> ScaleFrom | None:
    """An amount's own scale: its row's (JPM's "Debit and credit card sales volume (in billions)" in a table "(in
    millions)"), the heading row just above it inside the table (ServiceNow's results, a column "Amount ($ millions)"
    with "($ billions)" over its cRPO and RPO rows: $5.7 billion, never $5.7 million), its column's, or none for a
    per-unit amount (a price keeps its units). A per-unit amount never takes a heading row's scale, as it never takes a
    caption's: "(in millions, except per share amounts)" over FedEx's "Revenue per package" 2.15 is no $2.15 million."""
    per_unit = _PER_UNIT_KPI.search(kpi_label)
    if found := _row_scale_words(" ".join(table.rows[row])):
        return ScaleFrom(found[0], "row", found[1])
    if not per_unit and (found := _body_scale(table, row, col)[0]):
        return ScaleFrom(found[0], "heading", found[1])
    if found := _column_scale_words(table, row, col):
        return ScaleFrom(found[0], "column", found[1])
    if per_unit:
        return ScaleFrom(1.0, "per-unit", per_unit.group(0))
    return None


def _from_reader(document: Document, part, locator, value: float) -> tuple[ScaleFrom, list[str]]:
    """The reader's own scale where the document states none nearby: quoted words stand, anything else is held."""
    notes = _ai_scale_notes(document, part, locator.scale_text, locator.scale, value * locator.scale)
    quoted = bool(notes) and notes[0].startswith("scale quoted")
    return ScaleFrom(locator.scale, "quoted" if quoted else "reader", locator.scale_text), notes


def cell_amount_scale(document: Document, table: Table, row: int, col: int, kpi_label: str, locator,
                      value: float) -> tuple[ScaleFrom | None, list[str]]:
    """An amount's scale in a table: its own (row, column, per unit), else its table's caption or header, else the
    document's one scale; wording the reader quotes from beside the table beats a caption the table only inherits from
    the one before it (Goldman's "$ in billions" assets under supervision after its VaR table "$ in millions"). None when
    nothing states a scale and the reader gave none."""
    scale, notes = _cell_amount_scale(document, table, row, col, kpi_label, locator, value)
    # A scale heading a later heading may have ended ("($ billions)" above "Non-GAAP results:") that disagrees with the
    # scale found: which rows it heads the table does not say, so the figure is held, never served on either.
    passed = _body_scale(table, row, col)[1]
    if scale is not None and passed and passed[0] != scale.factor and scale.source not in ("row", "per-unit", "heading", "number"):
        notes = [*notes, f"{SCALE_HEADING_UNSURE}: {clean(passed[1])[:40]!r}"]
    return scale, notes


def _cell_amount_scale(document: Document, table: Table, row: int, col: int, kpi_label: str, locator,
                       value: float) -> tuple[ScaleFrom | None, list[str]]:
    own = _own_amount_scale(table, row, col, kpi_label)
    declared = own or (ScaleFrom(found[0], "table", found[1]) if (found := table_scale_words(table)) else None) \
        or (ScaleFrom(found[0], "document", found[1]) if (found := document_scale_words(document)) else None)
    if own is None and declared is not None and locator.scale in (1e3, 1e6, 1e9) and locator.scale != declared.factor \
            and _scale_quoted(document, table, locator.scale_text, locator.scale):
        return (ScaleFrom(locator.scale, "quoted", locator.scale_text),
                [f"scale quoted: {clean(locator.scale_text or '')[:40]!r} over the table's {declared.factor:g}"])
    if declared is not None:
        mismatch = locator.scale not in (None, declared.factor)
        return declared, [f"table declares scale {declared.factor:g}, AI said {locator.scale:g}"] if mismatch else []
    if locator.scale in (1, 1e3, 1e6, 1e9):
        return _from_reader(document, table, locator, value)
    return None, []


def passage_count_scale(before: str) -> ScaleFrom:
    """A count in a passage takes its label's own scale, as a table row's does: "Starlink Subscribers (#, in millions)
    12.0"."""
    found = _row_scale_words(before)
    return ScaleFrom(found[0], "row", found[1]) if found else ScaleFrom(1.0, "units")


def passage_amount_scale(document: Document, block: TextBlock, at: int, kpi_label: str, locator,
                         value: float) -> tuple[ScaleFrom | None, list[str]]:
    """An amount in a passage: a per-unit amount keeps its units (UTI's "$7,300" revenue per student under "($ in
    millions, except revenue per student amounts)"); otherwise the last scale heading before the quote in its block
    (Apollo's highlights page heads its earnings "($ in millions …)" and, further on, its AUM "($ in billions)"), the
    block's one declared scale, or the document's. Then the reader's own; None when nothing gives one."""
    if per_unit := _PER_UNIT_KPI.search(kpi_label):
        return ScaleFrom(1.0, "per-unit", per_unit.group(0)), []
    for source, found in (("passage", _preceding_scale_words(block.text, at)), ("block", block_scale_words(block)),
                          ("document", document_scale_words(document))):
        if found:
            return ScaleFrom(found[0], source, found[1]), []
    if locator.scale in (1, 1e3, 1e6, 1e9):
        return _from_reader(document, block, locator, value)
    return None, []


def reading_scale(document: Document, table: Table, row: int, col: int, locator) -> tuple[ScaleFrom | None, str | None]:
    """A whole breakdown table's amount scale: its column's, its caption's or header's, the document's, else a scale the
    reader quotes from the table. With the reason when there is none."""
    for source, found in (("column", _column_scale_words(table, row, col)), ("table", table_scale_words(table)),
                          ("document", document_scale_words(document))):
        if found:
            return ScaleFrom(found[0], source, found[1]), None
    if locator.scale in (1, 1e3, 1e6, 1e9):
        if locator.scale != 1 and not _scale_quoted(document, table, locator.scale_text, locator.scale):
            return None, f"scale {locator.scale:g} is not stated in the table: quote the words that state it in scale_text"
        return ScaleFrom(locator.scale, "quoted" if locator.scale != 1 else "reader", locator.scale_text), None
    return None, "amount scale unknown"
