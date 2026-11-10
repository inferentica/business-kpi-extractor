"""The checks on figures, in one engine. Each check declares what it applies to, and answers with findings: it changes
nothing itself. What comes of a finding (flag it, read again, ask the AI to explain it) is the caller's to decide.

How a reader reads stays with the reader: a chart's or a release table's rows must add up before they count as read at
all. These checks judge what has been read."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable

from .stated import stated_in_filing

MIX_TOLERANCE = 2.5  # percentage points; each rounded share can be off by half a point
BREAKDOWN_TOLERANCE = 0.01
PERIOD_TOLERANCE_DAYS = 12
MAX_RATIO_JUMP = 3.0
MAX_MIX_JUMP = 30.0
SMALL_COUNT = 50
SMALL_COUNT_MOVE = 25
MAX_DERIVED_GAP = 0.001
# A total this close to a figure reported in XBRL is that figure.
REPORTED_TOTAL_MATCH = 0.001
PERIOD_MATCH_DAYS = 12
# A breakdown row under this share of its total swings wildly and says nothing; the group's total still checks it.
IMMATERIAL = 0.01
# A quarter's total this many times the quarter's before it is an annual report read as a quarter.
FAR_ABOVE_QUARTER = 2.5
_SERIES_GAP_DAYS = 120


def _significant(value: float) -> bool:
    """A figure precise enough that equality with another is no coincidence (12,345,000,000, not 60,000,000,000)."""
    digits = f"{abs(value):.0f}".rstrip("0")
    return abs(value) >= 1e6 and len(digits) >= 3


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= max(abs(left), abs(right)) * 1e-9


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
    """The parts a total is made of: those the list puts at the top, and the lines of a parent not read this quarter."""
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
    for parent in figures:
        lines = [figure for figure in figures if figure.parent == parent.key]
        if not lines or not parent.value:
            continue
        error = (sum(figure.value for figure in lines) - parent.value) / parent.value
        if abs(error) > (MIX_TOLERANCE / 100 if parent.kind == "mix" else BREAKDOWN_TOLERANCE):
            found.append(Finding("lines_add_up", [parent, *lines], f"lines differ from {parent.label} by {error:.1%}"))
    return found


# The notes the jump checks below write. A jump is a sign of a misread, which a later filing stating the same figure
# disproves: TSMC's 3-nanometer revenue went from NT$0.5 billion to NT$29 billion as the node ramped.
JUMP_NOTE = re.compile(r"^(?:changed [\d.]+x|moved [+-]?[\d.,]+(?: points)?) in a quarter$")


def count_jump(unit: str, before: float, now: float) -> str | None:
    """Small counts move by a few at a time (ASML's used systems sold, 3 one quarter and 10 the next): below SMALL_COUNT
    a jump is an absolute move, not a ratio. Returns "" when the small-count rule applies and finds no jump."""
    if unit != "count" or max(abs(before), abs(now)) >= SMALL_COUNT:
        return None
    return f"moved {now - before:+g} in a quarter" if abs(now - before) > SMALL_COUNT_MOVE else ""


def _ratio_jump(before: float, now: float) -> str | None:
    ratio = now / before
    return f"changed {ratio:.1f}x in a quarter" if ratio > MAX_RATIO_JUMP or ratio < 1 / MAX_RATIO_JUMP else None


def _jump(figure: Figure, before: float | None) -> str | None:
    if before is None:
        return None
    if figure.kind == "mix":
        return f"moved {figure.value - before:+.1f} points in a quarter" if abs(figure.value - before) > MAX_MIX_JUMP else None
    if figure.unit == "percent" or before <= 0 or figure.value <= 0:
        return None
    small = count_jump(figure.unit, before, figure.value)
    return (small or None) if small is not None else _ratio_jump(before, figure.value)


def _no_sharp_change(figures: list[Figure], previous: dict[str, float], **_context) -> list[Finding]:
    """Against the quarter compared. An immaterial row swings wildly, and growth from an immaterial base is a launch
    (TSMC's 3nm: NT$0.5B to NT$29B), not an error."""
    group = figures[0].group
    parts_total = sum(figure.value for figure in _top_level([figure for figure in figures if not figure.is_total]))
    previous_total = sum(value for key, value in previous.items() if key.startswith(f"{group}."))
    found = []
    for figure in figures:
        breakdown = figure.kind == "revenue_breakdown"
        if breakdown and parts_total and abs(figure.value) < IMMATERIAL * abs(parts_total):
            continue
        before = previous.get(f"{group}.{figure.key}")
        if breakdown and before is not None and previous_total and abs(before) < IMMATERIAL * abs(previous_total):
            continue
        jump = _jump(figure, before)
        # A figure is judged by its filing, not by how far it moved: one the filing states where it was read stands.
        if jump and not stated_in_filing(figure.unit, figure.value, getattr(figure.source, "locator", None),
                                         getattr(figure.source, "notes", None), figure.label):
            found.append(Finding("no_sharp_change", [figure], jump))
    return found


_READ = frozenset({"ai"})
_BREAKDOWN = frozenset({"revenue_breakdown"})
_ADDS_UP = (Check("breakdown_adds_up", _breakdown_adds_up, _BREAKDOWN, _READ),)
READING_CHECKS = (
    Check("mix_adds_up", _mix_adds_up, frozenset({"mix"}), _READ),
    *_ADDS_UP,
    Check("lines_add_up", _lines_add_up, frozenset({"revenue_breakdown", "mix"}), _READ),
    Check("no_sharp_change", _no_sharp_change, None, _READ),
)


def reading_figures(items: list) -> list[Figure]:
    """One group's figures from a reading (extract.ReadValue)."""
    listed = {kpi.key: kpi.parent for kpi in items[0].group.kpis}
    return [Figure(group=item.group.key, kind=item.group.kind, key=item.kpi.key, unit=item.kpi.unit, value=item.value,
                   is_total=item.is_total or item.kpi.key == item.group.total_kpi, parent=item.kpi.parent, label=item.kpi.label,
                   listed_parents=listed, source=item) for item in items]


def _flag(items: Iterable, note: str) -> None:
    for item in items:
        item.status = "needs_review"
        item.notes.append(note)


def validate_groups(values: list, previous: dict[str, float]) -> None:
    """Marks every value of a group that fails its check as needs_review, with the reason."""
    by_group: dict[str, list] = {}
    for item in values:
        by_group.setdefault(item.group.key, []).append(item)
    for items in by_group.values():
        for finding in findings(READING_CHECKS, reading_figures(items), previous=previous):
            _flag((figure.source for figure in finding.figures), finding.note)


def check_period(located, expected: date) -> str | None:
    try:
        reported = date.fromisoformat(str(located.period_end)[:10])
    except ValueError:
        return "reported period missing"
    if abs((reported - expected).days) > PERIOD_TOLERANCE_DAYS:
        return f"reported period {reported} does not match the quarter ended {expected}"
    return None


_TOTAL_LABEL = re.compile(r"\btotal\b|^(?:net\s+)?(?:revenues?|sales)$", re.I)


def total_label(items) -> str:
    """The label of the row a whole-table reading took as its total (its parts' locators carry it)."""
    return next((str(item.locator["total_row_label"]) for item in items if item.locator.get("total_row_label")), "")


def looks_like_part_of_table(rows: int, total: float, rows_before: int, total_before: float | None,
                             total_label: str = "") -> bool:
    """Fewer rows than the quarter compared and a total under half of its: most likely part of a table, with a subtotal
    taken for the total (TSMC's 2nm and 3nm under "advanced technologies"). Its parts add up, so only this shows it.
    Size only prompts the look: a total read from the row the table itself labels as its total ("Total", "Net sales")
    is the whole table, however it compares with the quarter before."""
    if re.search(_TOTAL_LABEL, (total_label or "").strip(" :")):
        return False
    return bool(total_before) and rows < rows_before and total < 0.5 * total_before


def far_above_quarter(total: float, quarter_before: float | None) -> bool:
    return bool(quarter_before) and total >= FAR_ABOVE_QUARTER * quarter_before


def immaterial_row(value: dict) -> bool:
    """A stored breakdown row under 1% of its total, whose swings say nothing."""
    total = (value.get("locator") or {}).get("total")
    return value["group_kind"] == "revenue_breakdown" and bool(total) and abs(float(value["value"])) < IMMATERIAL * abs(float(total))


def _row_jump(current: dict, before: float, now: float) -> str | None:
    small = count_jump(current["unit"], before, now)
    if current["unit"] == "percent":
        return f"moved {now - before:+.1f} points in a quarter" if abs(now - before) > MAX_MIX_JUMP else None
    if small is not None:
        return small or None
    if before and now / before > 0 and not 1 / MAX_RATIO_JUMP <= now / before <= MAX_RATIO_JUMP:
        return f"changed {now / before:.1f}x in a quarter"
    return None


def date_order_findings(values: Iterable[dict], only: set[tuple] | None, key_of: Callable[[dict], tuple],
                        methods: frozenset[str] | None = _READ) -> dict[tuple, str]:
    """Each verified quarterly figure against the quarter before it, in date order: a sharp change, or the same significant
    figure as the quarter before or a year earlier (the tell of a prior-period column). `only`: the figures to judge."""
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
            jump = _row_jump(current, before, now)
            if jump and not immaterial_row(current) and not stated_in_filing(current["unit"], now, current.get("locator"),
                                                                              current.get("notes"), current.get("kpi_label")):
                flagged[key_of(current)] = jump
            elif (current["unit"] == "currency" and _significant(now) and _same(now, before) and not _row_shows(current, before, now)
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
                      method=row["method"], listed_parents=listed, source=row)
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
_STORED_CHECKS = tuple(check for check in READING_CHECKS if check.name != "no_sharp_change")
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
