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
from .quoted_rows import replay_row_line
from ..rules.period import DEEP_HEADER_ROWS
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
    # The scale words last quarter's reading quoted ("(in thousands)") go with the place: the scale check still holds the
    # figure unless the words are in this filing beside its table (UnitedHealth's members were held for an unstated scale).
    return Locator(kpi=key, table=table_id, row=row, col=col, scale=locator.get("scale"), scale_text=locator.get("scale_text"))


def row_gone(document: Document, locator: dict) -> bool:
    """Whether a row last quarter's reading had is in no table or passage of this filing: a line the company stopped
    reporting. A row carried onto the next page is still in the filing, so a table read only in part is never taken
    for a complete one."""
    label = clean(locator.get("row_label") or "")
    if not label:
        return False
    keys, text = _labels_of(document)
    return member_key(label) not in keys and label.lower() not in text


def _labels_of(document: Document) -> tuple[set[str], str]:
    """Every row label of the document and all its passages' text, worked out once for it (TSMC's reports hold some
    hundred tables: scanning them for each row took a document's replay from 120 ms to 300 ms at the 90th percentile)."""
    cached = getattr(document, "_labels_cache", None)
    if cached is None:
        keys = {member_key(cell) for table in document.tables.values() for row in table.rows for cell in row[:3] if cell}
        cached = (keys, "\n".join(block.text.lower() for block in document.blocks.values()))
        object.__setattr__(document, "_labels_cache", cached)
    return cached


def missing_since(last: dict[str, dict], keys: set[str], group: str, whole: bool, listed: set[str], document: Document) -> bool:
    """Whether a reading of a group lacks a figure the reference quarter had (a key the current list no longer has,
    from a reading under an older list, is not missing: it is not asked for; nor is a row the filing no longer has)."""
    return any(key.split(".", 1)[0] == group and key not in keys and (whole or key in listed) and not row_gone(document, locator)
               for key, locator in last.items())


def _replay_table(document: Document, group: str, parts: list[dict], quarter: int | None = None) -> TableLocator | str:
    first = parts[0]
    col = first.get("col")
    if col is None or "total_row_label" not in first:
        return "total row not recorded"  # read before replays recorded it
    misses = []  # why each table that has the rows does not give them, last quarter's first
    # A row the filing no longer has anywhere is gone, not unread (TSMC dropped its 10-nanometer row in 2024): the rest
    # are replayed, and still stand only where they add up to the total.
    wanted = {member_key(part["row_label"]) for part in parts if not row_gone(document, part)}
    if len(wanted) < 2:
        return "table rows not found"
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
                            total_row=total_row, scale=first.get("scale"), scale_text=first.get("scale_text"))
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
    value_token = numbers[index]
    found, value_group = _find_sentence(document, quote, tokens, value_token)
    if not found:
        row = replay_row_line(document, key, locator)  # a table row quoted as a line, whose signs or marks changed
        if not isinstance(row, str) or row != "sentence not found":
            return row
        # The words after the figure changed ("$316 billion, up 3% year-over-year" became "$321 billion, remained flat
        # year-over-year"): the sentence is found by its words up to the figure, the longest such start first.
        pinned = bool(_PERIOD_AFTER.search(quote, start + len(value_text)))
        for cut in _cuts(quote, start + len(value_text), tokens):
            found, value_group = _find_sentence(document, quote[:cut], [t for t in tokens if t.end() <= cut], value_token)
            if found:
                # A start no period word pins ("Revenue was $5.2 billion") is not the quarter's figure when the rest of
                # its new sentence names a span the old one did not ("... for the full year").
                if not pinned and len(found) == 1 and _new_span(document, found[0], quote[cut:]):
                    return "sentence not found"
                break
    if not found:
        return "sentence not found"
    places = [(block_id, match, prefix + match.group(value_group) + suffix) for block_id, match in found[:_MAX_PLACES]]
    places = [(block_id, match, value) for block_id, match, value in places if value in match.group(0)]
    if len(found) != 1:  # more than one sentence of that shape (this year's and last year's, a segment's table)
        return AmbiguousQuote([(Locator(kpi=key, block=block_id, quote=match.group(0), value_text=value),
                                section_span(document, block_id, match.start())) for block_id, match, value in places])
    if not places:
        return "sentence not found"
    block_id, match, new_value = places[0]
    return Locator(kpi=key, block=block_id, quote=match.group(0), value_text=new_value)


_MAX_PLACES = 8


class AmbiguousQuote(str):
    """"sentence found more than once", with each place it was found and the span its section names ("quarter",
    "longer" or None). The reading step keeps one place only on the filing's own evidence: its row's year-ago column
    equal to the stored year-ago figure, or its section naming the quarter (steps/replay_reading.py)."""
    candidates: list[tuple[Locator, str | None]]

    def __new__(cls, candidates: list[tuple[Locator, str | None]]):
        self = super().__new__(cls, "sentence found more than once")
        self.candidates = candidates
        return self


# A section's period, as the words before a place name it: a quarter ("second quarter 2023 results", "three months")
# or a longer span (Mastercard's "Year-to-date Results" and "Full Year Results" repeat the quarter's bullets).
_SECTION_SPAN = re.compile(r"\b(?:(?:first|second|third|fourth)[- ]quarter|q[1-4]|three months|"
                           r"(year[- ]to[- ]date|full[- ]year|six months|nine months|twelve months|fiscal year))\b", re.I)


def section_span(document: Document, block_id: str, start: int) -> str | None:
    """The period the section holding a place names: the last period words before it, in its own passage and then the
    passages before (tables are skipped: their column headers name every span they show)."""
    if block_id not in document.blocks:
        return None
    texts = [document.blocks[block_id].text[:start]]
    if block_id in document.order:
        at = document.order.index(block_id)
        texts += [document.blocks[key].text for key in reversed(document.order[:at]) if key in document.blocks]
    for text in texts:
        spans = list(_SECTION_SPAN.finditer(text))
        if spans:
            return "longer" if spans[-1].group(1) else "quarter"
    return None


def _find_sentence(document: Document, quote: str, tokens: list[re.Match], value: re.Match) -> tuple[list, int | None]:
    """Where the quote's sentence is in the filing, with its numbers and period words free: in the passages, else in
    the tables read as text (Visa's results sit in a one-column layout table, a reading the reader already takes).
    Strict first (only the value and period words change, so "3-nanometer" stays 3), then every number may change."""
    for texts in (lambda: ((block.id, block.text) for block in document.blocks.values()), lambda: _tables_as_text(document)):
        for loose in (False, True):
            pattern, position, group, value_group = [], 0, 0, None
            for token in tokens:
                pattern.append(re.escape(quote[position:token.start()]))
                if token is value or (loose and _is_number(token) and not _names(quote, token)):
                    group += 1
                    value_group = group if token is value else value_group
                    pattern.append(r"(\d[\d.,]*)")
                elif _is_number(token):
                    pattern.append(re.escape(token.group()))
                else:
                    pattern.append(r"[A-Za-z0-9]+")
                position = token.end()
            pattern.append(re.escape(quote[position:]))
            compiled = re.compile("".join(pattern), re.I)
            found = [(block_id, match) for block_id, text in texts() for match in compiled.finditer(text)]
            if found:
                return found, value_group
    return [], None


_SPAN = re.compile(r"\b(?:full[- ]year|(?:for|during|in) the year|annual|year[- ]to[- ]date|ytd|ltm|last twelve months|"
                   r"trailing|(?:three|six|nine|twelve) months|fiscal (?:year|20\d\d)|cumulative|since)\b", re.I)


def _new_span(document: Document, found: tuple[str, re.Match], dropped: str) -> bool:
    """Whether the rest of a shortened quote's new sentence names a period span its dropped words did not."""
    block_id, match = found
    text = next((t for i, t in _tables_as_text(document) if i == block_id), None) \
        if block_id not in document.blocks else document.blocks[block_id].text
    rest = re.split(r"(?<=[.;•])\s|•", (text or "")[match.end():], maxsplit=1)[0]
    spans = lambda words: {span.group(0).lower() for span in _SPAN.finditer(words)}
    return bool(spans(rest) - spans(dropped))


def _tables_as_text(document: Document) -> list[tuple[str, str]]:
    """Every table's cells as one text, as the reader reads a quote from a table (readers/extract.py), worked out once."""
    cached = getattr(document, "_table_text_cache", None)
    if cached is None:
        cached = [(table.id, clean(" ".join(cell for row in table.rows for cell in row if cell)))
                  for table in document.tables.values()]
        object.__setattr__(document, "_table_text_cache", cached)
    return cached


# Words that name a figure's period: a cut never drops the first of them after the figure ("in the quarter"), so a
# shortened sentence still says which period its figure is for. "Year-over-year", "the prior year" and "a year ago" are
# comparisons, not the figure's period.
_PERIOD_AFTER = re.compile(_PERIOD_WORD + r"|(?<![-\w])(?<!prior )(?<!last )(?<!previous )(?:quarters?|years?)(?![-\w])(?!\s+ago)"
                           r"|\b(?:ltm|months|annual|annually|ytd|year-to-date|full-year|period|ended|as of)\b", re.I)
_MIN_WORDS = 4


def _cuts(quote: str, value_end: int, tokens: list[re.Match]) -> list[int]:
    """Where last quarter's quote may end instead, longest first: after a word past the figure and the first period word
    that follows it, keeping at least four words other than period words so the start still names the figure."""
    floor = value_end
    period = _PERIOD_AFTER.search(quote, value_end)
    if period:
        floor = period.end()
    cuts = [match.start() for match in re.finditer(r"(?<=\S)\s", quote) if match.start() >= floor]
    if floor > value_end and floor < len(quote):
        cuts.append(floor)
    words = lambda text: [w for w in re.findall(r"[A-Za-z]{2,}", text) if not _PERIOD_AFTER.fullmatch(w)]
    return sorted({cut for cut in cuts if cut < len(quote) and len(words(quote[:cut])) >= _MIN_WORDS}, reverse=True)


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
    named = {_QUARTERS[match.group(1)] for match in _QUARTER_NAME.finditer(column_header(table, first_data_row, col, DEEP_HEADER_ROWS))}
    if named and quarter and named != {quarter}:
        return False
    raw_now = column_header(table, first_data_row, col)
    now, before = comparable_header(raw_now), comparable_header(locator["header"])
    return _FOOTNOTE.sub("", now) == _FOOTNOTE.sub("", before) \
        or same_period_words(now, before, cut=len(raw_now) >= _HEADER_CAP or len(locator["header"]) >= _HEADER_CAP)


# A footnote mark glued to a heading's word: Coca-Cola's "Unit Case Volume³" heads the column "Unit Case Volume" did.
_FOOTNOTE = re.compile(r"(?<=[a-z])#")
_HEADER_CAP = 118  # column_header keeps 120 characters: a header that long may end in a word cut short
# The spans a header names, each as its kind: what a column's period is, whatever title or unit words come with it.
_SPANS = [("quarter", r"\bthree months\b|<3 months>|<13 weeks>|\bquarters? ended\b|<(?:first|second|third|fourth) quarter>|<quarter>|\bquarterly\b"),
          ("half", r"\bsix months\b|<6 months>|<26 weeks>|\bhalf[- ]year"),
          ("nine", r"\bnine months\b|<9 months>|<39 weeks>"),
          ("year", r"\btwelve months\b|<12 months>|<5[23] weeks>|\byears? ended\b|\bfull[- ]year\b|\bfiscal year\b|\bannual\b|\bltm\b|\bttm\b"),
          ("to date", r"\byear[- ]to[- ]date\b|\bytd\b"),
          ("change", r"%|\bchange\b|\bvariance\b|\byoy\b|\bgrowth\b|\bvs\b|\bincrease\b|\bdecrease\b")]
# Words that say nothing of what a column measures: the period's own, and scale and unit words beside it.
_NOT_MEASURE = re.compile(r"^(?:three|six|nine|twelve|months?|weeks?|quarters?|quarterly|years?|ended|ending|fiscal|full|to|date|"
                          r"ytd|in|millions?|billions?|thousands?|dollars?|usd|unaudited|except|percentages?|per|share|data|"
                          r"and|of|the|as|at|for|amounts?)$")
# Words that change what a column measures when a header gains them: never a title.
_OTHER_MEASURE = re.compile(r"^(?:non-gaap|adjusted|organic|comparable|constant|neutral|excluding|normalized|underlying|core|"
                            r"pro-forma|proforma|segment|change)$")


def _spans(header: str) -> set[str]:
    return {kind for kind, pattern in _SPANS if re.search(pattern, header)}


def same_period_words(now: str, before: str, cut: bool = False) -> bool:
    """Whether two column headers head the same column though their words drifted (Visa's title row "Q3 FISCAL 2023"
    some quarters and not others, Morgan Stanley's table titles above "Quarter Ended"): they name the same spans, and
    one's measure words are the other's with title words in front. The spans must be named, both must name a measure,
    and no added word may change it (non-GAAP, adjusted, organic)."""
    if not _spans(now) or _spans(now) != _spans(before):
        return False

    def words(header: str) -> list[str]:
        found = re.findall(r"[a-z][a-z-]*[a-z]", _FOOTNOTE.sub("", header))
        found = found[:-1] if cut and found else found  # the last word may be cut short
        return [word for word in found if not _NOT_MEASURE.match(word)]
    a, b = words(now), words(before)
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if not short or long[len(long) - len(short):] != short:
        return False
    return not any(_OTHER_MEASURE.match(word) for word in long[:len(long) - len(short)])


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

    header_rows = table.rows[:max(first_data_row, 1)][:DEEP_HEADER_ROWS]
    column = years([row[col] for row in header_rows if col < len(row)])
    every = years([cell for row in header_rows for cell in row[1:]])
    if not every:
        return True  # no years in the header: the prior-period checks decide
    return bool(column) and max(column) == max(every)
