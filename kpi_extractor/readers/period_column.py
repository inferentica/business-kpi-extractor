"""Which column of a table holds the reported period's figure, by what its header names: the tests replay puts last
quarter's column to (the newest period, headed as before), and, when last quarter's column fails them, the one column
that names this filing's own period instead (period_column)."""
from __future__ import annotations

import re
from datetime import date

from ..documents.document import Table, parse_number
from ..quality.checks import PERIOD_TOLERANCE_DAYS
from ..rules.period import DEEP_HEADER_ROWS, column_dates, column_header
from ..rules.table import _heading

# A year may run into the word before it: Bank of America's supplement heads its quarters "SecondQuarter2025". A year's
# apostrophe may be curly: Citigroup's "3Q’23" read as no year at all, so its newest column failed the newest-column test.
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)|(?:\bQ[1-4]|\b[1-4]Q|\bFY)\s*['’]?(\d{2})\b|['’](\d{2})\b", re.I)


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
_QUARTER_NAME = re.compile(r"<?\b(first|second|third|fourth) ?quarter(?![a-z])>?")  # "secondquarter" run together too
_FULL_MONTH = ("january|february|march|april|june|july|august|september|october|november|december")


def comparable_header(header: str) -> str:
    """A recorded column header with what does not change the period taken out: which quarter it names, whether its
    date shows the day, and words a document ran together ("endedseptember", "monthsended"). Headers recorded before
    quarter names were kept ("#q#") compare too."""
    text = re.sub(rf"(?<=[a-z])(?=(?:{_FULL_MONTH})\b)|(?<=months)(?=ended)", " ", header)
    text = re.sub(rf"\b(?:{_FULL_MONTH})\b", "@", text)
    text = _QUARTER_NAME.sub("<quarter>", re.sub(r"(?<![\w<])#q#(?!\w)", "<quarter>", text))
    text = re.sub(r"(<quarter>)\s*['’]\s*#", r"\1", text)  # "3Q’23" recorded before the curly apostrophe counted
    return re.sub(r"#(?:\s*#)+", "#", text)


def current_column(table: Table, first_data_row: int, col: int) -> bool:
    """Whether a column is the newest period the table shows: when its header names years, it names the latest."""
    def years(texts: list[str]) -> set[int]:
        found = set()
        for text in texts:
            for four, short, apostrophe in _YEAR.findall(text):
                found.add(int(four) if four else 2000 + int(short or apostrophe))
        return found

    header_rows = table.rows[:max(first_data_row, 1)][:DEEP_HEADER_ROWS]
    # A header spanning several columns sits on the first of them, as column_header reads it: Goldman's "2026" over its
    # quarter's figures one cell to the right.
    column = years([_heading(row, col) for row in header_rows if col < len(row)])
    every = years([cell for row in header_rows for cell in row[1:]])
    if not every:
        return True  # no years in the header: the prior-period checks decide
    if not column and len(every) == 1:
        return True  # one year over the whole table (Mastercard's "For the 3 Months ended June 30, 2023"): no other period's column
    return bool(column) and max(column) == max(every)


def names_period(table: Table, row: int, col: int, expected: date, quarter: int | None, allowed: set[str] = frozenset(),
                 fiscal_year: int | None = None) -> bool:
    """Whether a column's header names the quarter ending `expected`: a quarter's span or none (never a change, a half,
    nine months, a year or a year to date), no word changing the measure (non-GAAP, adjusted) beyond `allowed`, and a
    date within the period tolerance of its end, or this fiscal quarter's name with exactly `fiscal_year` when it is
    known (for fiscal Q1 2025 ending 30 September 2024, "1Q25" and never "1Q24"), else this year or the next."""
    header = column_header(table, row, col, DEEP_HEADER_ROWS)
    if _spans(comparable_header(header)) - {"quarter"} or any(
            _OTHER_MEASURE.match(word) and word not in allowed for word in re.findall(r"[a-z][a-z-]*[a-z]", header)):
        return False
    dates, years = column_dates(table, row, col)
    if dates:
        return any(abs((day - expected).days) <= PERIOD_TOLERANCE_DAYS for day in dates)
    named = {_QUARTERS[match.group(1)] for match in _QUARTER_NAME.finditer(header)}
    allowed_years = {fiscal_year} if fiscal_year else {expected.year, expected.year + 1}
    return bool(quarter and named == {quarter} and years and set(years) <= allowed_years)


def period_column(table: Table, row: int, col: int, locator: dict, expected: date, quarter: int | None,
                  fiscal_year: int | None = None) -> int | None:
    """The one column of a row whose header names the filing's own period, when last quarter's column fails the tests
    above: a bank's supplement moves its quarters one column on each quarter ("3Q23 2Q23 1Q23 4Q22 3Q22"), and a table
    recorded under "Quarter Ended September 30" may head its columns "3Q23" the next. A column qualifies when it holds
    a figure, its header names a quarter's span or none (never a change, a half, nine months, a year or a year to
    date), gains no word that changes the measure (non-GAAP, adjusted), and names the period: a date within the period
    tolerance of its end, or this fiscal quarter's name with this fiscal year (`fiscal_year`; when unknown, this year or
    the next, as a fiscal year may be named for its end).
    Of two that qualify, the newest stands; otherwise none does. Last quarter's column must have been a quarter's."""
    before = comparable_header(locator.get("header") or "")
    if "header" not in locator or _spans(before) - {"quarter"}:
        return None
    words_before = set(re.findall(r"[a-z][a-z-]*[a-z]", before))
    # Last quarter's own column too: its header may only be reworded.
    found = [index for index in range(1, len(table.rows[row]))
             if parse_number(table.cell(row, index)) and names_period(table, row, index, expected, quarter, words_before, fiscal_year)]
    if len(found) > 1:
        found = [index for index in found if current_column(table, row, index)]
    return found[0] if len(found) == 1 else None
