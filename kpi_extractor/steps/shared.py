"""What several steps of a company's run need: names, stamps and helpers no one step owns."""
from __future__ import annotations

import re
from datetime import date

from ..ai import Spec
from ..archive import filing_folder
from ..checks import _days, _same, _significant  # noqa: F401 - used here and by the steps
from ..document import Document, clean, parse_number
from ..extract import column_header
from ..sec import FilingRef

AI_GROUP_PREFIX = "kpi_"
# Values read from a slide's chart: their own method, so a re-read of the release's text never withdraws them.
CHART_METHOD = "chart"
AI_GROUP_ORDER = 20
_DONE = {"processed", "needs_review", "skipped"}
_MAX_ATTEMPTS = 3
_READ_BY_AI = "read by AI"
_NOT_REPORTED = "not reported: "
_XBRL_READER = "xbrl reader 44"  # 21: segment operating income
_UNANCHORED = {"total matches no figure the company reported in XBRL", "no total revenue to reconcile against"}

def value_key(value: dict) -> tuple[str, str, str, str]:
    return value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"]


def only_adds(before: Spec, after: Spec) -> bool:
    """Whether a new list keeps every KPI of the old one as it was, adding only."""
    kept = {(group.key, group.kind, kpi.key, kpi.unit) for group in after.groups for kpi in group.kpis}
    return all((group.key, group.kind, kpi.key, kpi.unit) in kept for group in before.groups for kpi in group.kpis)

_ANNUAL_HEADER = re.compile(r"twelve months|<12 months>|<5[23] weeks>|fifty-(two|three) weeks|years? ended|fiscal years?|full year")
_QUARTER_HEADER = re.compile(r"three months|<3 months>|thirteen weeks|<1[34] weeks>|quarter|\bq[1-4]\b|\b[1-4]q\b")

def _annual_header(header: str) -> bool:
    return bool(_ANNUAL_HEADER.search(header)) and not _QUARTER_HEADER.search(header)


def _year_ago_cells(document: Document, locator: dict, value: float) -> list[float]:
    """The other cells of a value's row in columns headed for the same length of period ("three months ended"), in the
    value's own units: the year-ago figure (and the quarter before, where a release shows it)."""
    table = document.tables.get(str(locator.get("table")))
    if table is None or locator.get("row") is None or locator.get("col") is None:
        return []
    row, col = int(locator["row"]), int(locator["col"])
    current = parse_number(table.cell(row, col))
    if current is None or not current.value:
        return []
    factor = value / current.value
    first = next((i for i, r in enumerate(table.rows) if r and any(parse_number(c) for c in r[1:])
                  and re.search(r"[A-Za-z]", r[0]) and not all(re.fullmatch(r"(19|20)\d{2}", c.strip()) for c in r[1:] if c)), 1)
    period = column_header(table, first, col)  # "three months ended @ # #": years blanked, duration kept
    cells = []
    for other in range(len(table.rows[row])):
        text = table.cell(row, other) or ""
        cell = parse_number(text) if other != col and not re.search(r"[A-Za-z]", text) else None  # not the label's "(1)"
        if cell is not None and column_header(table, first, other) == period:
            cells.append(cell.value * factor)
    return cells


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
    return note.startswith(("changed ", "moved "))


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
