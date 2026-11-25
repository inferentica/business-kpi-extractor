"""Each owner line's source, worked out from the owner's own figures: the XBRL fact or table cell that equals the stored
figure in the filing it came from, kept only when the same source gives the stored figure in every recent quarter checked.

The hand builds were per-company scripts (concepts, members and release rows hard-coded in each); their output is what
is stored, with each row's filing. So a source is proven against that output rather than copied from the scripts: a line
whose figures no single fact or cell reproduces (a figure from a sentence, a sum the script made) is listed for the
owner, never guessed. A stored hand-written mapping (owner_sources/<SYMBOL>.json) is checked the same way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..documents.document import Document, clean
from .refresh import OwnerLine, _day
from .resolve import ONE, Fact, _amount, _covers, _first_data_row, resolve_table, resolve_xbrl, row_label
from .sources import GroupMapping, LineMapping, LineRef, OwnerSources, TableSource, XbrlSource

# Members a filer adds around the same figure in some filings and not others (Dell's segment facts carry
# OperatingSegmentsMember on ConsolidationItemsAxis from 2024): a mapping ignores them rather than requiring them.
WRAPPERS = ("OperatingSegmentsMember", "SegmentReportingSegmentsMember", "ReportableSegmentsMember")
_SCALES = (1.0, 1e3, 1e6, 1e9)
CHECKED = 4  # recent quarters a source must reproduce


@dataclass
class Evidence:
    """A stored figure and the filing it came from, ready to check a source against. The filing's facts and document are
    read when first asked for (a 10-Q's document is read only when no XBRL fact proves the line)."""
    row: dict
    ref: object  # FilingRef
    _facts: object = None  # list[Fact] | None, or a callable giving them
    _document: object = None  # Document | None, or a callable giving it

    @property
    def facts(self) -> list[Fact] | None:
        if callable(self._facts):
            self._facts = self._facts()
        return self._facts

    @property
    def document(self) -> Document | None:
        if callable(self._document):
            self._document = self._document()
        return self._document

    @property
    def end(self) -> date:
        return _day(self.row["period_end"])

    @property
    def duration(self) -> str:
        return "fy" if self.row["fiscal_period"] == "FY" else "quarter"

    @property
    def value(self) -> float:
        return float(self.row["value"])


@dataclass
class LineResult:
    key: str
    mapping: LineMapping | None = None
    checked: int = 0
    problem: str | None = None
    notes: list[str] = field(default_factory=list)


def _same(a: float, b: float, scale: float = 1.0) -> bool:
    return abs(a - b) <= max(0.5 * scale, 1e-9 * abs(b))


def _xbrl_variants(fact: Fact) -> list[XbrlSource]:
    members = dict(fact.dims)
    variants = [XbrlSource(concepts=[fact.concept], members=members)]
    wrapped = {axis: member for axis, member in members.items() if member not in WRAPPERS}
    if wrapped != members:
        variants.insert(0, XbrlSource(concepts=[fact.concept], members=wrapped,
                                      ignore=sorted({m for m in members.values() if m in WRAPPERS})))
    return variants


def xbrl_candidates(evidence: Evidence) -> list[XbrlSource]:
    """Sources a stored figure's own report holds it under: each fact of its duration equal to it."""
    if not evidence.facts:
        return []
    found: list[XbrlSource] = []
    for fact in evidence.facts:
        if _covers(fact, evidence.end, evidence.duration, False) and _same(fact.value, evidence.value):
            for variant in _xbrl_variants(fact):
                if variant not in found:
                    found.append(variant)
    # The fewest members first (the plain total before a member that happens to equal it).
    return sorted(found, key=lambda source: (len(source.members), -len(source.ignore)))


def _anchors(table, document: Document) -> list[str]:
    """Words before or atop a table that could name it: its caption's phrases, the last one first."""
    context = clean(table.context[-240:])
    phrases = [clean(part) for part in re.split(r"[.;:()\n]|\s{2,}|(?<=[a-z])\s(?=[A-Z]{2,}\b)", context)]
    phrases = [p for p in phrases if 2 <= len(p.split()) <= 9 and re.search(r"[A-Za-z]{3}", p)
               and not re.search(r"\bin (millions|thousands|billions)\b|unaudited|percent", p, re.I)]
    header = [clean(cell) for row in table.rows[:_first_data_row(table)] for cell in row if cell and len(cell.split()) >= 2
              and not re.search(r"\d{4}|months|weeks|ended", cell, re.I)]
    out = []
    for phrase in [*reversed(phrases), *header]:
        if phrase not in out:
            out.append(phrase)
    return out[:8]


def _section_of(table, row_index: int) -> str | None:
    """The heading above a row whose label repeats in its table ("Reconciliation to consolidated net revenue")."""
    label = row_label(table.rows[row_index][0])
    if sum(1 for row in table.rows if row and row_label(row[0]).lower() == label.lower()) < 2:
        return None
    for index in range(row_index - 1, -1, -1):
        row = table.rows[index]
        if row and clean(row[0]).endswith(":") and not any(_amount(cell) is not None for cell in row[1:]):
            heading = row_label(row[0])
            if heading.lower() not in ("net revenue", "operating income", "revenue", "revenues"):
                return re.escape(heading)
            continue
    return None


def table_candidates(evidence: Evidence) -> list[TableSource]:
    """Sources a stored figure's own document holds it under: each cell equal to it at the table's scale, in the column
    for its period."""
    document = evidence.document
    if document is None:
        return []
    found: list[TableSource] = []
    for table in document.tables.values():
        declared = table.declared_scale() or document.default_scale()
        for index, row in enumerate(table.rows):
            if not row or not row_label(row[0]):
                continue
            for cell in row[1:]:
                amount = _amount(cell)
                if amount is None:
                    continue
                for scale in ([declared] if declared else _SCALES):
                    if not _same(amount * scale, evidence.value, scale):
                        continue
                    label = row_label(row[0])
                    for anchor in _anchors(table, document):
                        source = TableSource(anchor=re.escape(anchor), row=re.escape(label), section=_section_of(table, index),
                                             scale=scale)
                        got = resolve_table(document, source, evidence.end, evidence.duration)
                        if got.status == ONE and _same(got.value, evidence.value, scale) and source not in found:
                            found.append(source)
                            break
    return found


def _resolves(source, evidence: Evidence) -> bool:
    if isinstance(source, XbrlSource):
        if evidence.facts is None:
            return False
        got = resolve_xbrl(evidence.facts, source, evidence.end, evidence.duration)
        return got.status == ONE and _same(got.value, evidence.value)
    got = resolve_table(evidence.document, source, evidence.end, evidence.duration)
    return got.status == ONE and _same(got.value, evidence.value, source.scale)


def proven(candidates: list, evidence: list[Evidence]) -> tuple[object | None, int]:
    """The first candidate that gives every stored figure checked, and how many it was checked on."""
    for candidate in candidates:
        if all(_resolves(candidate, item) for item in evidence):
            return candidate, len(evidence)
    return None, 0


def _prove(pool: list, evidence: list[Evidence], what: str, notes: list[str]) -> tuple[object | None, int]:
    """The first source in the pool that gives every stored figure checked; failing that, one that gives the newest
    (a line renamed or restated before it: Dell's "Corporate and other" took two lines' place in FY2025)."""
    source, checked = proven(pool, evidence)
    if source is None and len(evidence) > 1:
        source, checked = proven(pool, evidence[:1])
        if source is not None:
            notes.append(f"{what} checked on the newest quarter only: {len(evidence) - 1} earlier ones differ")
    if source is None:
        notes.append(f"no {what} gives the stored figures ({len(evidence)} checked)")
    return source, checked


def derive_line(line: OwnerLine, releases: list[Evidence], reports: list[Evidence], existing: LineMapping | None = None,
                balance: bool = False) -> LineResult:
    """A line's mapping: release cells from its quarters' releases, report facts (or cells) from its quarters' reports.
    A mapping written by hand is checked the same way, never replaced: what it fails is noted for the owner."""
    result = LineResult(line.key)
    if existing is not None and existing.hand and not existing.release and not existing.report:
        return LineResult(line.key, existing, 0, None, ["read by hand (as mapped)"])
    release = report = None
    if releases:
        pool = [existing.release] if existing and existing.release else [c for e in releases[:1] for c in table_candidates(e)]
        release, checked = _prove(pool, releases, "release table cell", result.notes)
        result.checked += checked
        if release is None and existing and existing.release:
            release = existing.release
    if reports:
        if existing and existing.report:
            pool = [existing.report]
        else:
            pool = [c for e in reports[:1] for c in xbrl_candidates(e)]
        report, checked = _prove(pool, reports, "XBRL fact", []) if pool else (None, 0)
        if report is None and not (existing and existing.report):
            # A figure the report prints but does not tag (Chevron's production, its 10-Q's segment table): its cell.
            report, checked = _prove([c for e in reports[:1] for c in table_candidates(e)], reports,
                                     "XBRL fact or report table cell", result.notes)
        result.checked += checked
        if report is None and existing and existing.report:
            report = existing.report
    if release is None and report is None:
        result.problem = "; ".join(result.notes) or "no stored figure from a filing to check a source against"
        return result
    result.mapping = LineMapping(release=release, report=report, hand=existing.hand if existing else None,
                                 balance=balance or bool(existing and existing.balance))
    return result


def derive_total(group: str, parts: dict[str, list[Evidence]], totals: list[tuple[Evidence, float]],
                 lines: dict[str, LineMapping], origin: str, existing: GroupMapping | None) -> object | None:
    """A group's total source: another line equal to it (a subtotal's parent), else a fact or cell equal to the stored
    total in the same filings."""
    current = (existing.release_total if origin == "release" else existing.report_total) if existing else None
    if current is not None:
        return current if isinstance(current, LineRef) or all(_resolves(current, _as_total(e, t)) for e, t in totals) else None
    if not totals:
        return None
    for key, evidence in parts.items():
        if key.split(".")[0] == group:
            continue
        stored = {(e.row["fiscal_year"], e.row["fiscal_period"]): e.value for e in evidence}
        if all(_same(stored.get((e.row["fiscal_year"], e.row["fiscal_period"]), float("nan")), t) for e, t in totals):
            return LineRef(line=key)
    first, value = totals[0]
    checks = [_as_total(e, t) for e, t in totals]
    source = None
    if origin == "report":
        source, _ = proven(xbrl_candidates(_as_total(first, value)), checks)
    if source is None:
        source, _ = proven(table_candidates(_as_total(first, value)), checks)
    return source


def _as_total(evidence: Evidence, total: float) -> Evidence:
    return Evidence({**evidence.row, "value": total}, evidence.ref, lambda: evidence.facts, lambda: evidence.document)


def sources_from(lines: dict[str, LineResult], groups: dict[str, GroupMapping], derived_from: str) -> OwnerSources:
    return OwnerSources(lines={key: r.mapping for key, r in lines.items() if r.mapping is not None}, groups=groups,
                        derived_from=derived_from)
