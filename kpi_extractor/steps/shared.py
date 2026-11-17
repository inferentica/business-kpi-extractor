"""What several steps of a company's run need: names, stamps and helpers no one step owns."""
from __future__ import annotations

import re
from datetime import date

from ..ai import Spec
from ..sources.archive import filing_folder
from ..quality.checks import _days, _same, _significant  # noqa: F401 - used here and by the steps
from ..quality.reasons import has, is_
from ..quality.stated import _names
from ..documents.document import Document, clean, parse_number
from ..readers.extract import column_header
from ..rules.period import DEEP_HEADER_ROWS, column_dates, release_unit
from ..readers.period_column import _QUARTER_NAME, _QUARTERS, _spans, comparable_header, current_column
from ..quality.checks import PERIOD_TOLERANCE_DAYS
from ..readers.quoted_rows import row_line_others
from ..sources.base import FilingRef

AI_GROUP_PREFIX = "kpi_"
# Values read from a slide's chart: their own method, so a re-read of the release's text never withdraws them.
CHART_METHOD = "chart"
AI_GROUP_ORDER = 20
_DONE = {"processed", "needs_review", "skipped"}
_MAX_ATTEMPTS = 3
_READ_BY_AI = "read by AI"
_NOT_REPORTED = "not reported: "
_XBRL_READER = "xbrl reader 50"  # 50: a split tagged alone beats one completed with a fixed member's siblings (Adobe)

def value_key(value: dict) -> tuple[str, str, str, str]:
    return value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"]


def _later_place(value: dict, known: dict | None) -> bool:
    """Whether a reading is a later place for its KPI than the one known: the later period, and on the same day the
    quarter's figure before the year's (a Q4 filing gives both; replay never starts from a year's place, so a year
    winning the tie lost TSMC's and ASML's Q4 places)."""
    if known is None:
        return True
    return (value["period_end"], value.get("fiscal_period") != "FY") > (known["period_end"], known.get("fiscal_period") != "FY")


def only_adds(before: Spec, after: Spec) -> bool:
    """Whether a new list keeps every KPI of the old one as it was, adding only."""
    kept = {(group.key, group.kind, kpi.key, kpi.unit) for group in after.groups for kpi in group.kpis}
    return all((group.key, group.kind, kpi.key, kpi.unit) in kept for group in before.groups for kpi in group.kpis)

_ANNUAL_HEADER = re.compile(r"twelve months|<12 months>|<5[23] weeks>|fifty-(two|three) weeks|years? ended|fiscal years?|full year")
_QUARTER_HEADER = re.compile(r"three months|<3 months>|thirteen weeks|<1[34] weeks>|quarter|\bq[1-4]\b|\b[1-4]q\b")

def _annual_header(header: str) -> bool:
    return bool(_ANNUAL_HEADER.search(header)) and not _QUARTER_HEADER.search(header)


def _year_ago_cells(document: Document, locator: dict, value: float,
                    period: tuple[date, int | None] | None = None) -> list[float]:
    """The other cells of a value's row in columns headed for the same length of period ("three months ended"), in the
    value's own units: the year-ago figure (and the quarter before, where a release shows it). With `period` (a
    quarter's end and its number), never a column naming another period: Truist's supplement lists five quarters, and
    its 2Q26 column is no figure for 3Q25."""
    table = document.tables.get(str(locator.get("table")))
    if table is None and locator.get("quote") and (parsed := parse_number(locator.get("value_text") or "")) and parsed.value:
        # A table row quoted as a line of text: its other figures (the year-ago one among them, where it shows one).
        unit = release_unit(value, parsed.value)
        return [] if unit is None else [other * unit for other in row_line_others(locator["quote"], locator["value_text"])]
    if table is None or locator.get("row") is None or locator.get("col") is None:
        return []
    row, col = int(locator["row"]), int(locator["col"])
    current = parse_number(table.cell(row, col))
    if current is None or not current.value:
        return []
    factor = release_unit(value, current.value)
    if factor is None:
        return []
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0]) and not all(re.fullmatch(r"(19|20)\d{2}", c.strip()) for c in r[1:] if c)), 1)
    span = column_header(table, first, col)  # "three months ended @ # #": years blanked, duration kept
    cells = {}
    for other in range(len(table.rows[row])):
        text = table.cell(row, other) or ""
        cell = parse_number(text) if other != col and not re.search(r"[A-Za-z]", text) else None  # not the label's "(1)"
        if cell is not None and column_header(table, first, other) == span:
            cells[other] = cell.value * factor
    if period is not None:
        cells = {other: cell for other, cell in cells.items() if not _another_period(table, first, other, *period)}
    return list(cells.values())


def repeated_in_quarter_columns(document: Document, locator: dict, value: float, expected: date, quarter: int) -> bool:
    """Whether a figure's row states the same number for another quarter while the column read names this one: its
    header names this quarter (by date or by number), and another column headed for a quarter, naming another period,
    holds the number too. A stale column read in place of this quarter's names another quarter, so never passes."""
    table = document.tables.get(str(locator.get("table")))
    if table is None or locator.get("row") is None or locator.get("col") is None:
        return False
    row, col = int(locator["row"]), int(locator["col"])
    current = parse_number(table.cell(row, col))
    factor = release_unit(value, current.value) if current is not None and current.value else None
    if factor is None:
        return False
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0]) and not all(re.fullmatch(r"(19|20)\d{2}", c.strip()) for c in r[1:] if c)), 1)

    def quarter_column(index: int) -> bool:
        return "quarter" in _spans(comparable_header(column_header(table, first, index))) or bool(_named_quarters(table, first, index))

    dates, _years = column_dates(table, first, col)
    names_this = (any(abs((day - expected).days) <= PERIOD_TOLERANCE_DAYS for day in dates) if dates
                  else _named_quarters(table, first, col) == {quarter})
    # Never the year-ago column, which may name the same quarter ("<third quarter>" over 3Q23 too): the newest year only.
    if not names_this or not quarter_column(col) or not current_column(table, first, col):
        return False
    for other in range(1, len(table.rows[row])):
        text = table.cell(row, other) or ""
        cell = parse_number(text) if other != col and not re.search(r"[A-Za-z]", text) else None
        if cell is not None and _same(cell.value * factor, value) and quarter_column(other) \
                and _another_period(table, first, other, expected, quarter):
            return True
    return False


def _named_quarters(table, row: int, col: int) -> set[int]:
    return {_QUARTERS[match.group(1)] for match in _QUARTER_NAME.finditer(column_header(table, row, col, DEEP_HEADER_ROWS))}


def _another_period(table, row: int, col: int, expected: date, quarter: int | None) -> bool:
    """Whether a column's header names some other period than the quarter ending `expected`: a date away from its end,
    or another quarter by number ("2Q26" for a third quarter). A header that cannot tell (a supplement's "4Q20" read
    as "fourth quarter" without its year) rules nothing out."""
    dates, _years = column_dates(table, row, col)
    if dates:
        return not any(abs((day - expected).days) <= PERIOD_TOLERANCE_DAYS for day in dates)
    named = {_QUARTERS[match.group(1)] for match in _QUARTER_NAME.finditer(column_header(table, row, col, DEEP_HEADER_ROWS))}
    return bool(quarter and named and quarter not in named)


def _chain_of(day: date) -> int:
    """Which reading chain a quarter belongs to: quarters ending in the same months of the year share one."""
    return day.month % 12 // 3


def _same_chain(period_end: str, day: date) -> bool:
    return _chain_of(date.fromisoformat(period_end[:10])) == _chain_of(day)


def _folder(ref: FilingRef) -> str:
    return filing_folder(ref.filed, ref.form, ref.accession)


def _identity_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _rounding(value: float) -> float:
    """Half the step a stated figure is rounded to ($5.96 billion: 5 million)."""
    whole, step = abs(int(round(value))), 1
    while whole and whole % (step * 10) == 0 and step < 10 ** 12:
        step *= 10
    return step / 2


def _is_jump(note: str) -> bool:
    return is_(note, "jump")


def _document_text(document: Document) -> str:
    cells = [" ".join(cell for row in table.rows for cell in row) for table in document.tables.values()]
    return clean(" ".join([*(block.text for block in document.blocks.values()), *cells]))


def list_parents(spec: Spec | None) -> dict[tuple[str, str], str]:
    """(stored group, KPI) → the KPI of its own group it is a line of."""
    return {(f"{AI_GROUP_PREFIX}{group.key}", kpi.key): kpi.parent for group in (spec.groups if spec else [])
            for kpi in group.kpis if kpi.parent}


class RecordBuilding:
    def _record(self, *, ref: FilingRef, period_end: date, source_url: str | None = None, **fields) -> dict:
        return {
            "symbol": self.symbol, **fields, "period_end": period_end.isoformat(),
            "source_accession": ref.accession, "source_url": source_url or ref.source_url,
            "filed_at": ref.filed.isoformat(),
        }


def held_for_later(value: dict, cells: list[float], checked: str) -> dict:
    """A held figure the next year's release gives otherwise, in more than one column: held for review, never set aside.
    Its own filing decides what it states. Goldman recast its 2022 banking lines when it formed Global Banking & Markets,
    and each 2022 figure, as its release stated it, was set aside as contradicted (2026-11-10)."""
    figures = ", ".join(f"{cell:,.6g}" for cell in cells[:3])
    notes = [*(value.get("notes") or []), f"the next year's release gives {figures} for this line: restated, or misread", checked]
    return {**value, "validation_status": "needs_review", "notes": notes}


def proof_tolerance(row: dict, usual: float) -> float:
    """How close a proof's figure must be: a figure one OCR model alone read only digit for digit, since a misread last
    digit hides inside any rounding (ASE's 21,932,480 read as 21,932,490 is 0.00005% off); any other, as usual."""
    return 1e-9 if has(row.get("notes"), "one_ocr_reader") else usual


def xbrl_figures(values) -> dict[bool, list[tuple[str, float, str | None]]]:
    """The figures the company reported in XBRL, by whether they are full years: each line's period end, value and
    label, and each split's total (labelled None: the company's total, whichever line it is the sum of)."""
    official: dict[bool, list[tuple[str, float, str | None]]] = {}
    for v in values:
        if v["method"] in ("xbrl", "derived") and not v["group_key"].startswith(AI_GROUP_PREFIX) and v["validation_status"] == "verified":
            figures = official.setdefault(v["fiscal_period"] == "FY", [])
            figures.append((v["period_end"], float(v["value"]), str(v.get("kpi_label") or "")))
            if (v.get("locator") or {}).get("total"):
                figures.append((v["period_end"], float(v["locator"]["total"]), None))
    return official


def printed_half(value: float, locator: dict | None) -> float:
    """Half the last place a figure was printed to, in its units: "$302 million" quoted is to the million, a table's
    1,200 in millions to the million (never to the hundred million its trailing zeros would say), "$5.9" billion to
    the hundred million. A figure with neither its text nor its scale (a recast, a tag) is to the unit."""
    from ..rules.precise_figures import _printed, printed_step
    locator = locator or {}
    text = str(locator.get("value_text") or "")
    if text and (raw := _printed(text)) and (step := printed_step(text)):
        return step * abs(value / raw) / 2
    unit = float(locator.get("scale") or 1)
    digits = f"{abs(value) / unit:.6f}".rstrip("0").rstrip(".")
    return unit * 10.0 ** -(len(digits.split(".")[1]) if "." in digits else 0) / 2


def adds_up_to_reported(official: dict, period_end: str, full_year: bool, parts: list[tuple[float, dict]],
                        stated: float | None) -> bool:
    """Whether a breakdown's parts add up to the line the company reported in XBRL as their total: the XBRL figure is
    the breakdown's own total as its release states it (two roundings of one figure, apart by less than both their
    half places), and the parts add up to it to their own printed places. With no total stated, only a sum equal to
    a line to the place the line is reported in. Never any line of the quarter to a share of the sum: parts adding to
    5,917 were once proven by an unrelated 5,940, and PNC's revenue by type (1,724) by its Retail Banking (1,723)."""
    total = sum(value for value, _locator in parts)
    if len(parts) < 2 or not total:
        return False
    places = [printed_half(value, locator) for value, locator in parts]
    halves, stated_half = sum(places), max(places)  # a stated total is printed to its parts' places
    for end, other, _label in official.get(full_year, []):
        if abs(_days(end, period_end)) > 12:
            continue
        # The XBRL figure's own place, to the million at the coarsest (TSMC tags its wafer revenue to NT$100,000): a
        # round 2,000,000,000 is no figure to the billion, and once proved JPM's banking fees of 2,479 million.
        own = min(_rounding(other), 5e5)
        if stated:
            if abs(stated - other) < stated_half + own and abs(total - other) <= halves + own:
                return True
        elif abs(total - other) <= own:
            return True
    return False


def equals_named_line(official: dict, row: dict) -> bool:
    """Whether a figure equals, to 0.01%, an XBRL line of its period whose label names it (Apple's Services, KKR's
    capital markets fees): the same line, reported twice. A figure that only equals some other line is a coincidence,
    no proof: Wells Fargo's Q4 2024 loss on debt securities, -$448 million, equals its segments' reconciling items."""
    figure, tolerance = float(row["value"]), proof_tolerance(row, 1e-4)
    return any(abs(_days(end, row["period_end"])) <= 12 and abs(other - figure) <= abs(figure) * tolerance and label is not None
               and _names(label, row.get("kpi_label"))
               for end, other, label in official.get(row["fiscal_period"] == "FY", []))
