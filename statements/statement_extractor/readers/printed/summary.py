"""Multi-year summary tables: an annual report's "Selected Financial Data", "Five-Year Summary" or "Ten-Year Financial
Summary". They reach years before a company's first statement on EDGAR (Apple's FY1997 10-K prints 1993-1997, Coca-Cola's
annual reports eleven years), so they give a company's key figures back towards its listing where no statement
survives. Only lines that name a key figure are kept (revenue, net income, total assets, equity, long-term debt): a
summary's ratios, per-share amounts and head counts are printed in other units and are left out. These are summary
figures, never statements, and are served as such."""
from __future__ import annotations

import re
import calendar
from dataclasses import replace
from datetime import date, timedelta

from ...domain.periods import Period, label_period
from ...domain.printed_labels import ASSETS, EQUITY, NET_INCOME, PER_SHARE
from .scales import statement_scale
from .tables import Table, figure_columns

_TITLE = re.compile(r"selected\s+(consolidated\s+)?financial\s+(data|information|highlights)|"
                    r"(statements?\s+of\s+(operations|income)|balance\s+sheets?|income\s+statement)\s+data\b|"
                    r"(five|ten)\s+fiscal\s+years|"
                    r"(five|six|ten|eleven)[-\s]year\s+(financial\s+)?(summary|review|data|comparison)|"
                    r"financial\s+(summary|highlights)|summary\s+of\s+(selected\s+)?(consolidated\s+)?financial\s+data|"
                    r"selected\s+(annual\s+)?data|historical\s+summary", re.I)
_KEYS = (
    ("revenue", re.compile(r"^(total\s+)?(net\s+)?(sales|revenues?|operating\s+revenues?|net\s+revenues?)(\s+and\s+other\s+income)?$|"
                           r"^total\s+net\s+(sales|revenues?)$", re.I)),
    ("net income", NET_INCOME),
    ("total assets", ASSETS),
    ("equity", re.compile(EQUITY.pattern + r"|^(share|stock)(holders|owners)['’]?\s+equity$|^shareowners['’]?\s+equity$", re.I)),
    ("long-term debt", re.compile(r"^long[-\s]term\s+(debt|borrowings)(\s*\(.*\))?$", re.I)),
)


def summary_tables(tables: list[Table]) -> list[Table]:
    """Tables titled as a multi-year summary, with at least three key lines printed for three or more years."""
    found = []
    for table in tables:
        title = table.context[-600:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2]) + \
            " " + " ".join(row.label for row in table.rows[:3] if not row.figures)
        if not _TITLE.search(title):
            continue
        keyed = sum(1 for row in table.rows if _key(row.label) and len(row.printed or row.figures) >= 3)
        if keyed >= 3 and _years(table):
            found.append(table)
    return found


_YEAR = r"(?:19[5-9]\d|20[0-4]\d)"
# A row of years over the columns: years apart only by spaces, a footnote mark or a currency ("1997 NT$ 1998" is not
# one, prose's "1997 and 1998" is not one).
_YEAR_ROW = re.compile(rf"(?:\b{_YEAR}(?:\s*\(\w\)|\*)?\s+){{2,}}\b{_YEAR}\b")


def _year_row(table: Table) -> list[str]:
    """The years over the columns, in order: the header's last row of years (as printed: two years can sit nearest
    one column), else the last one printed just before the
    table (a page's running header, or ASML's FY2001 20-F printing "1997 1998 1999 2000 2001" above each part's
    title). A translated last year ("2001 NT$ / 2001 US$") is printed twice."""
    header = table.header_text or " ".join(" ".join(cell.text for cell in cells) for cells in table.header)
    for text in [header, table.context[-500:]]:
        rows = _YEAR_ROW.findall(text)
        if rows:
            return re.findall(_YEAR, rows[-1])
    return []


def _years(table: Table) -> list[str]:
    """One year a column: as many of the year row's last years as the key lines print figures (Apple's FY1997 title
    "...ENDED SEPTEMBER 26, 1997" runs into its "1997 1996 1995 1994 1993")."""
    years = _year_row(table)
    counts = [len(row.printed) if row.printed else len(row.figures) for row in table.rows
              if (row.figures or row.printed) and _key(row.label)]
    width = max(set(counts), key=counts.count) if counts else 0
    if width < 3 or len(years) < width:
        return []
    return years[-width:]


_PER_SHARE_HEADING = re.compile(r"\bper\b.*\b(share|unit|ads)\b|\bper[-\s]share\b", re.I)
_CAPTION = re.compile(r"\bexcept\b|\bin\s+(millions|thousands|billions)\b", re.I)  # "(in millions, except per share)"
_BASIS = re.compile(r"^(u\.?s\.?|roc|r\.o\.c\.|canadian|dutch|netherlands|taiwan)?\s*(gaap|ifrs)$", re.I)


def _key(label: str) -> str | None:
    label = re.sub(r"\s*\.{2,}[\s.]*$", "", label).strip()
    if PER_SHARE.search(label):
        return None
    return next((name for name, pattern in _KEYS if pattern.match(label)), None)


def _by_year(row, table: Table, years: list[str]) -> dict[str, float]:
    """The row's figure for each year. A text line's figures are taken in printed order when there is one per year:
    its columns can drift from the year row's (TSMC's FY2001 20-F, set from a PDF, prints "Net sales.....  43,927
    50,524 ..." far right of its years). A text line with a figure missing is left out rather than guessed at. Table
    cells (HTML) are placed by their columns."""
    found: dict[str, float] = {}
    if row.printed or not row.figures:
        if len(row.printed) == len(years):
            for year, value in zip(years, row.printed):
                found.setdefault(year, value)  # a year printed twice: the second is the translation
        return found
    columns = figure_columns(table)
    if len(columns) == len(years):
        for year, column in zip(years, columns):
            value = next((row.figures[position] for position in column if position in row.figures), None)
            if value is not None and year not in found:
                found[year] = value
    return found


def read_summaries(tables: list[Table], *, accession: str, form: str, filed: date, fiscal_year_end: str,
                   year_offset: int) -> list[dict]:
    """Each year's key figures from every summary table: {fiscal_year, period_end, name, value, label, ...}. Lines
    under a second basis (TSMC's "US GAAP" lines after its "ROC GAAP" ones) are a reconciliation and left out."""
    figures = []
    for table in summary_tables(tables):
        scale = statement_scale([table])
        if scale is None:
            continue  # no stated scale: a figure in thousands read as millions is worse than none
        years, first_basis, basis, seen, per_share = _years(table), None, None, set(), False
        for number, row in enumerate(table.rows):
            below = table.rows[number + 1] if number + 1 < len(table.rows) else None
            if not row.label.strip() and below is not None and not below.figures and _key(below.label):
                row = replace(row, label=below.label)  # a label set below its figures (ASML's FY2001 "Total assets")
            if not row.figures and not row.printed:
                if _BASIS.match(row.label.strip(" :")):
                    basis = re.sub(r"\W", "", row.label.lower())
                    first_basis = first_basis or basis
                elif row.label.strip():
                    # Lines under "Per fully diluted share:" are per share, whatever they are called (Brookfield's
                    # "Net income  1.02").
                    per_share = bool(_PER_SHARE_HEADING.search(row.label)) and not _CAPTION.search(row.label)
                continue
            if per_share:
                continue
            name = _key(row.label)
            if name is None or name in seen or basis != first_basis:
                continue
            values = _by_year(row, table, years)
            if not values:
                continue
            seen.add(name)
            for year, value in values.items():
                month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
                end = date(int(year), month, min(day, calendar.monthrange(int(year), month)[1]))
                labelled = label_period(Period(end, end - timedelta(days=363)), fiscal_year_end, year_offset)
                if labelled is None or labelled[1] != "FY":
                    continue
                figures.append({"fiscal_year": labelled[0], "period_end": end.isoformat(), "name": name,
                                "table": f"{accession}#{table.index}", "value": value * scale, "label": row.label[:120],
                                "accession": accession, "form": form, "filed": filed.isoformat(), "scale": scale})
    return figures
