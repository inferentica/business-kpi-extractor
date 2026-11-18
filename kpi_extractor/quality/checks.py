"""The checks on figures, in one engine. Each check declares what it applies to, and answers with findings: it changes
nothing itself. What comes of a finding (flag it, look for proof in the filings) is the caller's to decide.

How a reader reads stays with the reader: a chart's or a release table's rows must add up before they count as read at
all. These checks judge what has been read."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable

from ..rules.numbers import same_figure
from .reasons import OFF_ROW, PART_ROW, SHARED_CELL

MIX_TOLERANCE = 2.5  # percentage points; each rounded share can be off by half a point
BREAKDOWN_TOLERANCE = 0.01
PERIOD_TOLERANCE_DAYS = 12
MAX_DERIVED_GAP = 0.001
# A total this close to a figure reported in XBRL is that figure.
REPORTED_TOTAL_MATCH = 0.001
PERIOD_MATCH_DAYS = 12
_SERIES_GAP_DAYS = 120


def _significant(value: float) -> bool:
    """A figure precise enough that equality with another is no coincidence (12,345,000,000, not 60,000,000,000)."""
    digits = f"{abs(value):.0f}".rstrip("0")
    return abs(value) >= 1e6 and len(digits) >= 3


_same = same_figure


def _days(left: str, right: str) -> int:
    return (date.fromisoformat(left[:10]) - date.fromisoformat(right[:10])).days


@dataclass
class Figure:
    """One figure as the checks see it, from a reading or a stored row. `source` is what the caller gets back."""
    group: str
    kind: str
    key: str
    unit: str
    value: float
    is_total: bool = False
    parent: str | None = None
    label: str = ""
    method: str = "ai"
    # What the group's list says each KPI is a line of, for a parent that was not read.
    listed_parents: dict[str, str | None] = field(default_factory=dict)
    source: object = None
    # Where it was read: its filing and the table cell, so a cell read twice counts once.
    cell: tuple | None = None


def cell_of(locator: object, accession: object = None) -> tuple | None:
    if not isinstance(locator, dict) or locator.get("table") is None or locator.get("row") is None or locator.get("col") is None:
        return None
    return (accession, str(locator["table"]), int(locator["row"]), int(locator["col"]))


def _once(figures: list[Figure]) -> list[Figure]:
    """One figure per cell: UnitedHealthcare's Employer & Individual row was read as the list's Domestic and again under
    its own name, and counted twice its parts overshot the total by 36%. A listed KPI's reading is the one kept."""
    first: dict[tuple, Figure] = {}
    for figure in sorted(figures, key=lambda item: item.key not in item.listed_parents):
        if figure.cell is not None:
            first.setdefault(figure.cell, figure)
    return [figure for figure in figures if figure.cell is None or first[figure.cell] is figure]


@dataclass
class Finding:
    check: str
    figures: list[Figure]
    note: str


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[..., Iterable[Finding]]
    kinds: frozenset[str] | None = None  # the kinds of group it applies to; None: all
    methods: frozenset[str] | None = None  # the ways of reading it applies to; None: all

    def applies(self, figure: Figure, widened: bool = False) -> bool:
        """widened: what the check would see if every way of reading faced it, for a report on what that would change."""
        return (self.kinds is None or figure.kind in self.kinds) and (widened or self.methods is None or figure.method in self.methods)


def findings(checks: Iterable[Check], figures: list[Figure], widened: bool = False, **context) -> list[Finding]:
    """Every finding on one group's figures, in the order the checks are listed."""
    found: list[Finding] = []
    for check in checks:
        subject = [figure for figure in figures if check.applies(figure, widened)]
        if subject:
            found += check.run(subject, **context)
    return found


def _top_level(parts: list[Figure]) -> list[Figure]:
    """The parts a total is made of: those the list puts at the top, and the lines of a parent not read this quarter,
    each cell once."""
    parts = _once(parts)
    read = {figure.key for figure in parts}

    def counted(figure: Figure) -> bool:
        parent = figure.parent
        # A line counts in place of its parent when the parent was not read (nor any parent above it).
        while parent is not None:
            if parent in read:
                return False
            parent = next((other.parent for other in parts if other.key == parent), None) or figure.listed_parents.get(parent)
        return True
    return [figure for figure in parts if counted(figure)]


def _mix_adds_up(figures: list[Figure], **_context) -> list[Finding]:
    # A mix's own "Total 100%" row (its named total) is its check, not one of its shares.
    shares = _top_level([figure for figure in figures if not figure.is_total])
    stated = next((figure.value for figure in figures if figure.is_total), None)
    if any(not 0 <= figure.value <= 100 for figure in figures):
        reason = "share outside 0-100%"
    elif stated is not None and abs(stated - 100) > MIX_TOLERANCE:
        reason = f"the mix's total row reads {stated:.1f}%"
    else:
        total = sum(figure.value for figure in shares)
        reason = f"shares add up to {total:.1f}%" if abs(total - 100) > MIX_TOLERANCE else None
    return [Finding("mix_adds_up", figures, reason)] if reason else []


def _breakdown_adds_up(figures: list[Figure], **_context) -> list[Finding]:
    total = next((figure for figure in figures if figure.is_total), None)
    parts = _top_level([figure for figure in figures if not figure.is_total])
    if total is None or not total.value:
        reason = "no total revenue to reconcile against"
    elif len(parts) < 2:
        reason = "fewer than two parts"
    else:
        error = (sum(figure.value for figure in parts) - total.value) / total.value
        reason = f"parts differ from the total by {error:.1%}" if abs(error) > BREAKDOWN_TOLERANCE else None
    return [Finding("breakdown_adds_up", figures, reason)] if reason else []


def _lines_add_up(figures: list[Figure], **_context) -> list[Finding]:
    """Each parent the list names against its lines: a parent and its lines that do not add up need review."""
    found = []
    figures = _once(figures)
    for parent in figures:
        lines = [figure for figure in figures if figure.parent == parent.key]
        if not lines or not parent.value:
            continue
        error = (sum(figure.value for figure in lines) - parent.value) / parent.value
        if abs(error) > (MIX_TOLERANCE / 100 if parent.kind == "mix" else BREAKDOWN_TOLERANCE):
            found.append(Finding("lines_add_up", [parent, *lines], f"lines differ from {parent.label} by {error:.1%}"))
    return found




_READ = frozenset({"ai"})
_BREAKDOWN = frozenset({"revenue_breakdown"})
_ADDS_UP = (Check("breakdown_adds_up", _breakdown_adds_up, _BREAKDOWN, _READ),)
READING_CHECKS = (
    Check("mix_adds_up", _mix_adds_up, frozenset({"mix"}), _READ),
    *_ADDS_UP,
    Check("lines_add_up", _lines_add_up, frozenset({"revenue_breakdown", "mix"}), _READ),
)


def reading_figures(items: list) -> list[Figure]:
    """One group's figures from a reading (extract.ReadValue)."""
    listed = {kpi.key: kpi.parent for kpi in items[0].group.kpis}
    return [Figure(group=item.group.key, kind=item.group.kind, key=item.kpi.key, unit=item.kpi.unit, value=item.value,
                   is_total=item.is_total or item.kpi.key == item.group.total_kpi, parent=item.kpi.parent, label=item.kpi.label,
                   listed_parents=listed, source=item, cell=cell_of(item.locator)) for item in items]


def _flag(items: Iterable, note: str) -> None:
    for item in items:
        item.status = "needs_review"
        item.notes.append(note)




def _shared_cells(values: list) -> None:
    """Two listed metrics read from one table cell: at most one of them is that cell's figure. Exxon's six "Worldwide"
    rows (production, throughput, sales) were each read from the capital expenditure table's "Worldwide" row, 7,159,
    and all six passed every check. Both are held. A found row on a listed metric's cell is its copy, not a second
    metric (UnitedHealthcare's Employer & Individual, read as Domestic and again under its own name). A figure read in
    full elsewhere still holds the rounded cell it was read from: Truist's summary row "Deposits" is its average deposits,
    and the period-end deposits read from that row too stay held once the average moves to its full figure (the average,
    read in full on a row naming it, is not)."""
    by_cell: dict[tuple, list] = {}
    for item in values:
        if not any(kpi.key == item.kpi.key for kpi in item.group.kpis):
            continue
        cells = {cell_of(item.locator), cell_of(item.locator.get("rounded_from") or {})} - {None}
        for cell in cells:
            by_cell.setdefault(cell, []).append(item)
    for cell, items in by_cell.items():
        if len({(item.group.key, item.kpi.key) for item in items}) > 1:
            # The one read in full from a row naming its own metric is that cell's; the others are held.
            moved = [item for item in items if cell_of(item.locator) != cell]
            held = [item for item in items if cell_of(item.locator) == cell] if len(moved) == 1 else items
            _flag((item for item in held if SHARED_CELL not in item.notes), SHARED_CELL)


def _row_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def _off_row(values: list) -> None:
    """A group whose lines are a table's columns (State Street's net flows by asset class: Equity, Fixed-Income, Cash
    across one "Total flows, net" row) has each line read from that one row; a line read from another row is held
    (Cash, read from the "Cash fund" row, the one row naming it). Only when at least two lines share a row that names
    none of them: a group whose lines are rows has no shared row."""
    by_group: dict[str, list] = {}
    for item in values:
        if item.locator.get("row_label"):
            by_group.setdefault(item.group.key, []).append(item)
    for items in by_group.values():
        rows: dict[str, list] = {}
        for item in items:
            rows.setdefault(_row_key(str(item.locator["row_label"])), []).append(item)
        shared, sharers = max(rows.items(), key=lambda pair: len(pair[1]))
        columns = [item for item in sharers if shared and shared not in _row_key(item.kpi.label)
                   and _row_key(item.kpi.label) not in shared]
        if len(columns) < 2 or len(sharers) * 2 < len(items):
            continue
        _flag((item for item in items if _row_key(str(item.locator["row_label"])) != shared and OFF_ROW not in item.notes),
              OFF_ROW)


def _part_rows(values: list) -> None:
    """A line read from the row of one of its parts: KeyCorp's "Time Deposits" from its "Other time deposits" row, one
    of the two (beside "Certificates of deposit ($100,000 or more)"), 3,209 of 6,192 in Q4 2020."""
    for item in values:
        row = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", str(item.locator.get("row_label") or "").lower())).strip()
        line = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", item.kpi.label.lower())).strip()
        if line and not line.startswith("other") and re.fullmatch(rf"(?:all )?other {re.escape(line)}s?", row) \
                and PART_ROW not in item.notes:
            _flag([item], PART_ROW)


def validate_groups(values: list, previous: dict[str, float]) -> None:
    """Marks every value of a group that fails its check as needs_review, with the reason."""
    by_group: dict[str, list] = {}
    for item in values:
        by_group.setdefault(item.group.key, []).append(item)
    for items in by_group.values():
        for finding in findings(READING_CHECKS, reading_figures(items), previous=previous):
            _flag((figure.source for figure in finding.figures), finding.note)
    _shared_cells(values)
    _off_row(values)
    _part_rows(values)


def check_period(located, expected: date) -> str | None:
    try:
        reported = date.fromisoformat(str(located.period_end)[:10])
    except ValueError:
        return "reported period missing"
    if abs((reported - expected).days) > PERIOD_TOLERANCE_DAYS:
        return f"reported period {reported} does not match the quarter ended {expected}"
    return None


def date_order_findings(values: Iterable[dict], only: set[tuple] | None, key_of: Callable[[dict], tuple],
                        methods: frozenset[str] | None = _READ) -> dict[tuple, str]:
    """Each verified quarterly figure against the quarter before it and a year earlier, in date order: the same
    significant figure as either (the tell of a prior-period column). How far a figure moved is never a finding: the
    owner's rule (2026-11-09) judges a figure only by its filing. `only`: the figures to judge."""
    series: dict[tuple[str, str], list[dict]] = {}
    for v in values:
        if (methods is None or v["method"] in methods) and v["validation_status"] == "verified" and v["fiscal_period"] != "FY":
            series.setdefault((v["group_key"], v["kpi_key"]), []).append(v)
    flagged: dict[tuple, str] = {}
    judged = lambda row: only is None or key_of(row) in only  # noqa: E731
    for rows in series.values():
        rows.sort(key=lambda v: v["period_end"])
        for previous, current in zip(rows, rows[1:]):
            if not judged(current) or _days(current["period_end"], previous["period_end"]) > _SERIES_GAP_DAYS:
                continue
            before, now = float(previous["value"]), float(current["value"])
            if (current["unit"] == "currency" and _significant(now) and _same(now, before) and not _row_shows(current, before, now)
                  and not states_one_period(current)):
                flagged[key_of(current)] = "the same figure as the quarter before: check the column"
        by_end = {v["period_end"][:7]: v for v in rows}
        for current in rows:
            year_ago = by_end.get(f"{int(current['period_end'][:4]) - 1}{current['period_end'][4:7]}")
            if (judged(current) and year_ago and current["unit"] == "currency" and _significant(float(current["value"]))
                    and _same(float(current["value"]), float(year_ago["value"]))
                    and not _row_shows(current, float(year_ago["value"]), float(current["value"]))
                    and not states_one_period(current)):
                flagged[key_of(current)] = "the same figure as a year earlier: check the column"
    return flagged


# Words that put an earlier period beside the figure in a quote ("up from $108 billion a year ago"): only then could the
# figure read be the earlier one. A growth rate ("up 3% year-over-year") names no earlier figure.
_EARLIER = re.compile(r"\b(?:a year (?:ago|earlier)|year[- ]ago|prior[- ](?:year|quarter|period)|previous (?:year|quarter)|last "
                      r"(?:year|quarter)|same (?:period|quarter) (?:of|last)|compared (?:to|with)|versus|vs\.?|(?:up|down) from|from \$)",
                      re.I)
_YEAR = re.compile(r"(?<![\d.,$])(?:19|20)\d{2}(?![\d.,])")


def states_one_period(current: dict) -> bool:
    """Whether a figure's source states only its own period, so a repeat of an earlier period's figure is the company's
    own and not an earlier column or clause read by mistake (APO's fee-generating credit AUM, $135 billion in Q3 2025 and
    Q3 2026, read from a chart that shows only the quarter): a table row with one figure, or a quote that names no
    earlier period or year. Owner, 2026-11-10: a repeat alone is no reason to hold a figure its filing states."""
    locator = current.get("locator") or {}
    numbers = [number for number in locator.get("row_numbers") or [] if number is not None]
    if numbers:
        return len(numbers) == 1
    quote = str(locator.get("quote") or "")
    if not quote:
        return False
    years = {int(year) for year in _YEAR.findall(quote)}
    own = {int(str(current.get("period_end") or "0")[:4]), int(str(current.get("fiscal_year") or "0") or 0)}
    return not _EARLIER.search(quote) and years <= own


def _row_shows(current: dict, other: float, now: float) -> bool:
    """Whether the row a figure was read from prints the other period's figure too, so the repeat is the company's own
    (PNC's card and cash management, 737 in both quarters) and not a column read twice. The row's numbers travel with
    the locator as printed; the read cell comes first, so the scale is their ratio."""
    numbers = (current.get("locator") or {}).get("row_numbers") or []
    if len(numbers) < 2 or not numbers[0]:
        return False
    scale = now / float(numbers[0])
    return any(n is not None and abs(float(n) * scale - other) <= abs(other) * 1e-6 for n in numbers[1:])


def reported_matches(total: float, end: str, reported: list[tuple[str, float, str | None]]) -> tuple[bool, list[str | None]]:
    """Whether figures were reported for the period, and the members of those a breakdown's total equals (None for revenue
    itself rather than a segment). Reported: (period end, figure, member)."""
    nearby = [(figure, member) for figure_end, figure, member in reported if abs(_days(figure_end, end)) <= PERIOD_MATCH_DAYS]
    return bool(nearby), [member for figure, member in nearby if abs(figure - total) <= abs(total) * REPORTED_TOTAL_MATCH]


def derived_gap(parts: float, total: float) -> tuple[float, bool]:
    """How far a worked-out period's parts are from its worked-out total, and whether that is too far."""
    error = (parts - total) / total
    return error, abs(error) > MAX_DERIVED_GAP


def row_path(row: dict) -> str:
    """How a stored figure came to be: the way of reading a widened check would newly face."""
    locator = row.get("locator") or {}
    for mark, name in (("release_table", "release table"), ("quarter_table", "quarter table"), ("quoted_row", "repeated row"),
                       ("tagged", "XBRL tag"), ("prospectus", "prospectus"), ("year_ago", "year-ago column")):
        if locator.get(mark):
            return name
    return {"ai": "AI reading", "xbrl": "XBRL", "derived": "worked out", "chart": "chart"}.get(row["method"], row["method"])


def row_figures(rows: list[dict], parents: dict[tuple[str, str], str] | None = None) -> list[Figure]:
    """One group's period from stored rows, its stated total as its total row. Stored rows do not say what they are a
    line of: `parents` gives it from the KPI list, (group, key) → parent."""
    parents = parents or {}
    listed = {key: parent for (group, key), parent in parents.items() if group == rows[0]["group_key"]}
    figures = [Figure(group=row["group_key"], kind=row["group_kind"], key=row["kpi_key"], unit=row["unit"], value=float(row["value"]),
                      parent=listed.get(row["kpi_key"]) or (row.get("locator") or {}).get("parent"), label=row["kpi_label"],
                      method=row["method"], listed_parents=listed, source=row,
                      cell=cell_of(row.get("locator"), row.get("source_accession")))
               for row in rows]
    total = next((t for row in rows if isinstance(t := (row.get("locator") or {}).get("total"), (int, float))), None)
    if total is not None:
        figures.append(Figure(group=rows[0]["group_key"], kind=rows[0]["group_kind"], key="", unit=rows[0]["unit"], value=float(total),
                              is_total=True, method=rows[0]["method"]))
    return figures


def parts_add_up(rows: list[dict], parents: dict[tuple[str, str], str] | None = None) -> bool:
    """Whether a stored breakdown period's parts make its stated total. One with no stated total has nothing to miss."""
    return not any(finding.note.startswith("parts differ")
                   for finding in findings(_ADDS_UP, row_figures(rows, parents), widened=True))


# Run on what is already stored: "no total" and "fewer than two parts" only mean a reading is incomplete.
_STORED_CHECKS = READING_CHECKS
_ONLY_WHEN_READING = ("no total revenue", "fewer than two parts")


def audit_stored(values: Iterable[dict], key_of: Callable[[dict], tuple],
                 parents: dict[tuple[str, str], str] | None = None) -> list[dict]:
    """What every check would say about the figures now served if it applied to every way of reading and every period.
    Changes nothing: a report for deciding which checks to widen. A figure the owner decided is not questioned."""
    served = [row for row in values if row["validation_status"] == "verified"]
    periods: dict[tuple, list[dict]] = {}
    for row in served:
        periods.setdefault((row["group_key"], row["fiscal_year"], row["fiscal_period"]), []).append(row)
    report = []
    for rows in periods.values():
        for finding in findings(_STORED_CHECKS, row_figures(rows, parents), widened=True):
            if finding.note.startswith(_ONLY_WHEN_READING):
                continue
            report += [{"check": finding.check, "note": finding.note, "row": figure.source} for figure in finding.figures
                       if figure.source is not None and not figure.source.get("owner_decision")]
    by_key = {key_of(row): row for row in served}
    for key, note in date_order_findings(served, None, key_of, methods=None).items():
        if not by_key[key].get("owner_decision"):
            report.append({"check": "date_order", "note": note, "row": by_key[key]})
    return report
