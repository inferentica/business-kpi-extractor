"""Period rules: which period a column or a quote gives (a quarter, six or nine months, the year to date, the year),
the dates its header names, and the KPIs that are levels at a period's end."""

from __future__ import annotations

from datetime import date, timedelta
import math
import re

from ..ai import KpiSpec
from ..documents.document import clean, parse_number, Table
from .scale import _PER_UNIT_KPI
from .table import _heading


# A week-based quarter is 12, 13, 14 or 16 weeks (PepsiCo, Costco, Kroger); its half and three quarters 24 to 28 and
# 36 to 40 weeks.
_QUARTER = re.compile(r"<3 months>|three months|<1[2-6] weeks>|(twelve|thirteen|fourteen|sixteen) weeks|quarter|\bq[1-4]\b|\b[1-4]q\b")


# "ytd" and "ltm" head Digital Realty's leasing columns beside each quarter's.
_LONGER = re.compile(r"<(6|9|12) months>|(six|nine|twelve) months|<(2[4-8]|3[6-9]|40|5[23]) weeks>|"
                     r"(twenty-four|twenty-six|twenty-eight|thirty-six|thirty-nine|forty|fifty-two|fifty-three) weeks|"
                     r"years? ended|year to date|fiscal years?|\bytd\b|\bltm\b|last twelve months|trailing twelve months")


_FOURTH_QUARTER = re.compile(r"fourth quarter", re.I)


_ORDINAL = {"1": "first", "2": "second", "3": "third", "4": "fourth"}


def _stands_at_period_end(kpi: KpiSpec) -> bool:
    """A count or amount that is a level at the period's end (beds, deposits, backlog), never a ratio or a per-unit amount."""
    return kpi.unit in ("count", "currency") and bool(_STOCK_KPI.search(kpi.label)) and not _PER_UNIT_KPI.search(kpi.label) \
        and not _CHANGE_KPI.search(kpi.label) and not re.search(r"\b(?:added|awarded|new|sold|sales|revenue|gross adds)\b", kpi.label, re.I)


def column_duration_problem(header: str, annual: bool) -> str | None:
    """Why a column is not the period being read, judged from its header alone: a six-, nine- or twelve-month column
    when a quarter is expected, or a quarter's column when the year is. Silent headers pass."""
    quarter, longer = bool(_QUARTER.search(header)), bool(_LONGER.search(header))
    if not annual and longer and not quarter:
        return f"the column is headed {header!r}, not a quarter"
    if annual and quarter and not longer:
        return f"the column is headed {header!r}, not the year"
    return None


# A count that is a change over the quarter (net additions, net change), which a fall makes negative.
_CHANGE_KPI = re.compile(r"\bnet\s+(?:additions?|adds|change|gains?|increase)\b|\bchange\b", re.I)


# A figure for another period than the quarter: "for the first six months of 2023", "for fiscal year 2024".
_OTHER_PERIOD = re.compile(r"\bfirst (?:six|nine) months\b|\b(?:six|nine|twelve) months ended\b|\bfor the (?:full )?(?:fiscal )?year\b"
                           r"|\bfull[- ]year\b|\bfiscal (?:year )?\d{4}\b|\byear[- ]to[- ]date\b|\bYTD\b|\bfirst half\b|\bH1\b", re.I)

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\u2022(])|\u2022|\s[\u2013\u2014-]\s")


def sentence_opening(text: str, quote: str) -> str:
    """The words of the quote's sentence before the quote: "" when the quote begins its sentence, or is not found. Meta's
    Q4 2023 release opens "For the full year 2023, ad impressions increased by 28% ... and the average price per ad
    decreased by 9%": a quote of its second half alone read the year's -9% as the quarter's +2%."""
    at = text.find(quote)
    if at <= 0:
        return ""
    return _SENTENCE_END.split(text[:at])[-1].strip()


# A slide title naming the year to date ("2025 YTD Contracting Statistics").
_YTD_TITLE = re.compile(r"(?:\b20\d{2}\s+)?\bYTD\b|\b[Yy]ear[- ]to[- ][Dd]ate\b")


# A KPI defined over a longer span than the quarter, which such wording describes rather than contradicts.
_LONG_SPAN_KPI = re.compile(r"year[- ]to[- ]date|\bYTD\b|cumulative|all[- ]time|trailing|\bTTM\b|\bLTM\b|twelve months|full[- ]year|annual|"
                            r"current[- ]year|first[- ]half|second[- ]half|\bH[12]\b|since (?:inception|launch)|to date", re.I)


_NAMES_QUARTER = re.compile(r"\b(?:first|second|third|fourth)\s+quarter\b|\bquarter\s+(?:ended|of)\b|\bthree months\b|\b[1-4]Q\b|\bQ[1-4]\b", re.I)


# A KPI over the twelve months to the quarter's end (KKR's "Private Equity Portfolio (LTM Gross Return)"), and the words
# that give a twelve-month figure: the year ended at a fourth quarter is its last twelve months.
_TRAILING_KPI = re.compile(r"\bLTM\b|\bTTM\b|\btrailing\b|\btwelve[- ]months?\b|\b12[- ]months?\b", re.I)
_TWELVE_MONTHS = re.compile(r"\bLTM\b|\bTTM\b|\btrailing\b|twelve[- ]months?|\b12[- ]months?|\byears? ended\b|\bfor the (?:full )?year\b"
                            r"|\b(?:during|over|in) the (?:full )?year\b|\bfull[- ]year\b|\byear[- ]over[- ]year\b|\bannual", re.I)


def quarter_for_trailing_kpi(before: str, kpi_label: str) -> str | None:
    """Why a quote's figure is not a twelve-month KPI's: the words before it name the quarter and no twelve months.
    KKR's "For the quarter ended December 31, 2016, KKR's private equity portfolio appreciated 3.4%" is the quarter's
    return; its LTM return is the year's 11.9%, in the sentence before."""
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z])", before)[-1]  # the year's figure in the sentence before is another's
    if _TRAILING_KPI.search(kpi_label) and _NAMES_QUARTER.search(sentence) and not _TWELVE_MONTHS.search(sentence):
        return f"the quote gives the quarter's figure, not {kpi_label!r}"
    return None


# A KPI that is a level at period end (a stock), not a flow over it: a year-to-date change can lead to its level.
_STOCK_KPI = re.compile(r"backlog|obligations?|\bRPO\b|\bcRPO\b|customers?|subscribers?|members?|users?|base\b|balances?|assets|"
                        r"\bAU[MSA]\b|in force|count\b|sites|rigs|fleet|stores|locations|employees|headcount|deposits|loans|capacity|"
                        r"properties|units in operation|policies", re.I)


# A table row that is a year, not a quarter: Sun Communities' operating statistics by year (2025, 2024, 2023).
_YEAR_ROW = re.compile(r"^\s*(?:FY\s?|fiscal\s+)?(?:19|20)\d\d\s*$", re.I)


_MONTH_NAME = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|" \
              r"jul|aug|sept?|oct|nov|dec)\.?"


_MONTH_SPAN = re.compile(rf"\b{_MONTH_NAME}\s*(?:to|through|thru|-|–|—)\s*{_MONTH_NAME}(?![\w])")


# The header rows a recorded header reads (stored readings compare with it as it was), and the deeper reach of the
# period checks: JPMorgan's supplement tables open with four title rows, so their "2Q24 1Q24 ..." row is the seventh.
HEADER_ROWS, DEEP_HEADER_ROWS = 6, 12


def column_header(table: Table, first_data_row: int, col: int, rows: int = HEADER_ROWS) -> str:
    """The words heading a column ("Three Months Ended @ #") with dates and numbers blanked, so a quarter's column and a
    six-month or prior-year column never pass for one another when a reading is replayed."""
    cells = [cell for row in table.rows[:max(first_data_row, 1)][:rows] if col < len(row) and re.search(r"[A-Za-z]", cell := _heading(row, col))]
    text = clean(" ".join(cells)).lower()
    # A span of months names its length: Philips' "January to June" is a half-year, "April to June" a quarter.
    text = _MONTH_SPAN.sub(lambda m: f"<{(_MONTHS[m.group(2)[:3]] - _MONTHS[m.group(1)[:3]]) % 12 + 1} months>", text)
    text = re.sub(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                  r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", "@", text)
    # A duration names the column ("3 months", "six months", "26 weeks"), so its number is kept; dates and years blank.
    text = re.sub(r"(?<!<)\b(\d{1,2})(\s*-?\s*)(months?|weeks?|quarters?)\b(?!>)", r"<\1 \3>", text)
    # A quarter's label keeps its meaning once its digits blank: Scotiabank's "Q4" and Digital Realty's "3Q26" were "q#"
    # and "#q#", which the period gate could not read, so a fourth quarter's ratio was stored as the fiscal year's.
    text = re.sub(r"\b(?:q([1-4])|([1-4])q)(?:\s*'?\s*(?:fy)?\s*\d{2,4})?\b", lambda m: f"<{_ORDINAL[m.group(1) or m.group(2)]} quarter>", text)
    return re.sub(r"(?<![<\d])[\d.,]+(?![\d]* (?:months?|weeks?|quarters?)>)", "#", text)[:120]


_MONTHS = {name: index for index, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
     ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec")], 1)
    for name in names}


_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})\b")


_FULL_DATE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b")


_YEAR = re.compile(r"(?<![\d$.,])(?:fy\s?)?'?((?:19|20)\d{2})(?![\d%]|[,.]\d)|\b[1-4]q'?(\d{2})\b|\bq[1-4]\s?'?(\d{2}|(?:19|20)\d{2})\b")


def column_dates(table: Table, first_data_row: int, col: int) -> tuple[list[date], list[int]]:
    """The dates and years a column's own header cells name, read from the header rows above the first data row
    (the words column_header keeps, here with their dates): "Three Months Ended September 30," over "2026" gives
    2026-09-30. A two-digit year ("3Q25", "Q3'25") counts as 20xx."""
    cells = [_heading(row, col) for row in table.rows[:max(first_data_row, 1)][:6] if col < len(row)]
    text = clean(" ".join(cell for cell in cells if cell)).lower()
    dates = []
    for month, day, year in _FULL_DATE.findall(text):
        try:
            dates.append(date(int(year), _MONTHS[month], int(day)))
        except ValueError:
            continue
    for month, day, year in _NUMERIC_DATE.findall(text):  # PepsiCo's "12 Weeks Ended 6/14/2025"
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    years = sorted({int(full) if full else 2000 + int(short or quarter[-2:]) for full, short, quarter in _YEAR.findall(text)
                    if (full or short or quarter)})
    return dates, years


def header_contradicts(table: Table, row: int, col: int, expected: date, *also: date) -> str | None:
    """Why a value's column is not the reported quarter's, from the dates its header names; None when the header
    agrees or names none. A full date must be within ten days of the quarter's end. A header naming only years
    contradicts only when every year is before the quarter's own (the year-ago column): fiscal years named ahead of
    the calendar (Apple's Q1 FY2027 ends in December 2026) never do."""
    dates, years = column_dates(table, row, col)
    if dates:
        if any(abs((day - end).days) <= 10 for day in dates for end in (expected, *also)):
            return None
        return f"column headed {max(dates).isoformat()}, not the quarter ended {expected.isoformat()}"
    if years and max(years) < (expected - timedelta(days=10)).year:
        return f"column headed {max(years)}, not the quarter ended {expected.isoformat()}"
    return None


def release_unit(value: float, cell: float) -> float | None:
    """The release's own unit (a power of ten) between a stored figure and its cell. A figure since restated differs
    from its cell (Bank of America's Q4 2019 equities: $1,015 million stored, $1,018 million in that release), so the
    ratio itself would scale every other cell of the row off its filing ($1,071 million read as $1,067.84 million)."""
    if not value or not cell:
        return None
    unit = 10.0 ** round(math.log10(abs(value / cell)))
    return unit if abs(value / cell / unit - 1) <= 0.1 else None


def year_ago_value(table: Table, row: int, col: int, period_end: date | None, value: float) -> float | None:
    """The row's figure for the same period a year before, in the read figure's units: the one other cell under a
    column of the same length ("three months ended", "<second quarter>") whose header names the period a year earlier
    (its date about 365 days back, or its year one before the read column's). None when no column, or more than one,
    qualifies: the release then states no comparative this can vouch for."""
    if period_end is None or row >= len(table.rows) or col >= len(table.rows[row]):
        return None
    own = parse_number(table.rows[row][col])
    if own is None or not own.value:
        return None
    factor = release_unit(value, own.value)
    if factor is None:
        return None
    length = column_header(table, row, col)
    own_years = column_dates(table, row, col)[1]
    target = period_end - timedelta(days=365)
    found = []
    for other, text in enumerate(table.rows[row]):
        if other == col or not text or re.search(r"[A-Za-z]", text) or column_header(table, row, other) != length:
            continue
        cell = parse_number(text)
        if cell is None or cell.percent != own.percent:
            continue
        dates, years = column_dates(table, row, other)
        if dates:
            earlier = any(abs((day - target).days) <= 20 for day in dates)
        else:
            earlier = len(years) == 1 and bool(own_years) and years[0] == max(own_years) - 1
        if earlier:
            found.append(cell.value * factor)
    return round(found[0], 6) if len(found) == 1 else None


def year_ago_record(table: Table | None, row: int, col: int, period_end: str | None, value: float) -> dict:
    """A table reading's locator field for the release's own figure a year before, kept as what that release reports
    for the earlier period (TradeTracker's business_kpi_reported): {"year_ago_value": ...}, or nothing."""
    try:
        end = date.fromisoformat(str(period_end)[:10]) if period_end else None
    except ValueError:
        end = None
    earlier = year_ago_value(table, row, col, end, value) if table is not None else None
    return {"year_ago_value": earlier} if earlier is not None else {}


_STATES_QUARTER = re.compile(r"\bthree months\b|\b(?:1[23]|thirteen|twelve) weeks\b|\bquarter\b|\b[1-4]q(?:'?\d{2,4})?\b|\bq[1-4](?:'?\d{2,4})?\b", re.I)


def states_quarter(header: str) -> bool:
    """Whether a column header itself says its figures are a quarter's ("Three Months Ended", "Q4 2025", "4Q25"):
    the filing's own word on the period, which a total far above the quarter before cannot outweigh."""
    return bool(_STATES_QUARTER.search(header or ""))


def other_period_problem(text: str, quote: str, before: str, kpi_label: str) -> str | None:
    """Why a quoted figure is not the quarter's, when the words before it give another period. "YTD 2025, the company was
    awarded 14 new contract commitments" is the year to date, not the quarter's count, unless the KPI itself is defined
    over the longer span (Tesla's cumulative deliveries, PGIM's trailing flows). A quote that names the quarter ("In the
    second quarter of fiscal 2026, 2,148 units booked") is the quarter's figure. The period may open the quote's
    sentence before the quote itself (sentence_opening)."""
    context = f"{sentence_opening(text, quote)} {before}"
    if (span := _OTHER_PERIOD.search(context[-120:])) and not _LONG_SPAN_KPI.search(kpi_label) \
            and not _NAMES_QUARTER.search(context[-160:]):
        # GE Vernova's "backlog increased $6.5 billion year-to-date, to approximately $26 billion" states a change over
        # the year and then the level at quarter end: the level of a stock measure is the quarter's figure.
        between = context[-120:][span.end():]
        level = re.search(r"\b(?:to|reached|reaching|stood at|stands at|as of|at the end|ended the quarter with|now)\b", between)
        if not (level and _STOCK_KPI.search(kpi_label)):
            return f"the quote gives the figure {span.group(0).lower()!r}, not the quarter's"
    return None
