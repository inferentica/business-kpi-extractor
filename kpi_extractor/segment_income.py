"""Each segment's operating income, as a report tags it (US filers since ASU 2023-07: OperatingIncomeLoss on the business
segments axis), with what the segments leave out of the company's operating income as rows of their own.

Alphabet's segments are Google Services, Google Cloud and Other Bets; its "Alphabet-level activities" (shared AI research,
about −$7.7 billion a quarter in 2026) are not a segment, so the segments add up to more than operating income. The
filing's reconciling items keep their own names, and so do the cost lines its reconciliation tags (Palantir's research
and development, general and administrative, and stock-based compensation); only what no filing line accounts for is
"Unallocated". The rows always add up to the operating income the report states."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Callable

import pandas as pd

from .xbrl import _axis_name, _member_labels, humanize, local_name, member_key, remove_subtotals

INCOME_CONCEPT = "us-gaap:OperatingIncomeLoss"
GROUP_KEY = "segment_income"
GROUP_LABEL = "Operating Income by Segment"
_SEGMENT_AXES = {"StatementBusinessSegmentsAxis", "SegmentsAxis"}
_NEUTRAL = {("ConsolidationItemsAxis", "OperatingSegmentsMember")}
# Items the consolidation axis tags between the segments and operating income, under the filing's own name when it has
# a short one.
_RECONCILING = {"CorporateNonSegmentMember": "Corporate", "MaterialReconcilingItemsMember": "Reconciling items",
                "SegmentReconcilingItemsMember": "Reconciling items", "IntersegmentEliminationMember": "Eliminations"}
_LEFT_OVER = "Unallocated"
# A company's own segment measures, when it tags no operating income by segment (Brookfield Asset Management's
# "Segment Earnings", its fee-related earnings, over its "Segment Revenue").
_OWN_PROFIT = re.compile(r"^[\w-]+:Segment(?:Earnings|Profit|ProfitLoss|OperatingIncome(?:Loss)?)$")
_OWN_REVENUE = re.compile(r"^[\w-]+:SegmentRevenues?$")
# A company's own segment contribution (Palantir's "pltr:Contribution", each segment's revenue less its own costs, since
# its first inline-XBRL 10-Q): its filing reconciles it to operating income, so the rows do too, with the costs it
# leaves out under the names its reconciliation gives them.
_CONTRIBUTION = re.compile(r"^[\w-]+:(?:Segment)?Contribution$")
# A company's own segment revenue is its revenue by segment: it stands in for the GAAP revenue its notes split by
# segment (Brookfield's leaves out its share of the managers it does not consolidate).
REVENUE_GROUP_KEY = "segments"
REVENUE_GROUP_LABEL = "Revenue by Segment"
LEFT_OVER_KEY = member_key(_LEFT_OVER)
# How close a reconciliation's tagged lines must come to the gap: filings round each line to their own unit.
_LINES_CLOSE = 0.001
# Sets of more lines than this are not searched: a fit among so many could be chance.
_MOST_LINES = 10


def line_key(name: str) -> str:
    """A reconciling line's identity across filings, whose names for it vary: Palantir's "Research and development",
    "Research and development expenses" and "Research and Development Expense Excluding Stockbased Compensation Expense"
    are one series, as are "Stock-based compensation expense" and "Total stock-based compensation expense"."""
    base = re.sub(r"^total\s+", "", name, flags=re.I)
    base = re.sub(r"\s+excluding\b.*$", "", base, flags=re.I)
    base = re.sub(r"\s+(?:expenses?|costs?)$", "", base, flags=re.I)
    return member_key(re.sub(r"stock-?based", "stockbased", base, flags=re.I))


@dataclass
class IncomeRows:
    """One period's rows: (key, label, element or None, value)."""
    rows: list[tuple[str, str, str | None, float]]
    total: float
    # The keys of the rows that are lines of the filing's reconciliation, not segments.
    lines: set[str] = field(default_factory=set)


@dataclass
class SegmentIncome:
    current: IncomeRows
    year_to_date: IncomeRows | None = None
    prior: IncomeRows | None = None
    elements: dict[str, str | None] = field(default_factory=dict)
    concept: str = INCOME_CONCEPT
    label: str = GROUP_LABEL


def _period(frame: pd.DataFrame, end: date, low: int, high: int) -> pd.DataFrame:
    ends = pd.to_datetime(frame["period_end"], errors="coerce")
    days = (ends - pd.to_datetime(frame["period_start"], errors="coerce")).dt.days
    return frame[(ends.dt.date == end) & days.between(low, high)]


def _reconciling_facts(facts: pd.DataFrame, skip: set[str]) -> pd.DataFrame:
    """Facts tagged with a reconciling item of the consolidation axis and nothing else, in any other concept: the cost
    lines between the segments' measure and operating income (Palantir tags its research and development so)."""
    columns = [column for column in facts.columns if column.startswith("dim_")]
    axis = next((column for column in columns if _axis_name(column) == "ConsolidationItemsAxis"), None)
    if axis is None:
        return facts.iloc[0:0]
    others = [column for column in columns if column != axis]
    tagged = facts[axis].map(lambda member: pd.notna(member) and local_name(str(member)) in _RECONCILING)
    alone = facts[others].isna().all(axis=1) if others else pd.Series(True, index=facts.index)
    return facts[tagged & alone & facts["numeric_value"].notna() & (facts["period_type"] == "duration") & ~facts["concept"].isin(skip)]


def _closing_lines(frame: pd.DataFrame | None, gap: float, line_label: Callable[[str], str | None]) -> list[tuple[str, str, None, float]]:
    """The period's tagged reconciling lines as rows, when exactly one set of them is the gap. A report states them as
    costs, so they count against operating income. Revenue, capital spending and the like share the axis (Nvidia tags
    its corporate depreciation there too), so the set is looked for; two sets that both fit, a total beside its parts,
    name nothing."""
    if frame is None or frame.empty:
        return []
    found: dict[str, float] = {}
    for concept, value in zip(frame["concept"].astype(str), frame["numeric_value"]):
        if float(value):
            found.setdefault(concept, float(value))
    if not found:
        return []
    named = {concept: line_label(concept) for concept in found}
    found = {concept: value for concept, value in found.items() if named[concept]}
    if len(found) > _MOST_LINES:
        return []
    tolerance = max(abs(gap) * _LINES_CLOSE, 1.0)
    closing = [(chosen, costs) for size in range(1, len(found) + 1) for chosen in combinations(found, size)
               for costs in (True, False) if abs(gap + (1 if costs else -1) * sum(found[concept] for concept in chosen)) <= tolerance]
    if len(closing) != 1:
        return []
    chosen, costs = closing[0]
    return [(line_key(named[concept]), named[concept], None, -found[concept] if costs else found[concept])
            for concept in sorted(chosen, key=lambda concept: -abs(found[concept]))]


def _rows(frame: pd.DataFrame, label: Callable[[str], str | None], labels: dict[str, str],
          children: dict[str, set[str]], summed: bool = False, lines: pd.DataFrame | None = None,
          line_label: Callable[[str], str | None] = lambda _qname: None) -> IncomeRows | None:
    columns = [column for column in frame.columns if column.startswith("dim_")]
    total = None
    segments: dict[str, float] = {}
    reconciling: dict[str, float] = {}
    for _, fact in frame.iterrows():
        tagged = {_axis_name(column): str(fact[column]) for column in columns if pd.notna(fact[column])}
        dims = {axis: member for axis, member in tagged.items() if (axis, local_name(member)) not in _NEUTRAL}
        value = float(fact["numeric_value"])
        if not tagged:
            total = value if total is None else total
        elif not dims:
            continue  # the segments' own sum (Nvidia's "Operating Segments"), not the company's operating income
        elif len(dims) == 1 and next(iter(dims)) in _SEGMENT_AXES:
            segments.setdefault(next(iter(dims.values())), value)
        elif set(dims) == {"ConsolidationItemsAxis"} and local_name(dims["ConsolidationItemsAxis"]) in _RECONCILING:
            reconciling.setdefault(dims["ConsolidationItemsAxis"], value)
        elif len(dims) == 2 and "ConsolidationItemsAxis" in dims and local_name(dims["ConsolidationItemsAxis"]) in _RECONCILING \
                and (segment_axis := next((axis for axis in dims if axis in _SEGMENT_AXES), None)):
            # A reconciling item tagged with a member of the segments axis takes that member's name (AMD's "All Other",
            # a corporate item on the segments axis since 2025).
            reconciling.setdefault(dims[segment_axis], value)
    # A segment that is others added up (Intel's "Total Intel Products" over CCPG and DCAI), declared or by its figures,
    # would count them twice: only the leaves stay.
    segments = {member: value for member, value in segments.items()
                if not (children.get(member, set()) & (set(segments) - {member}))}
    segments = remove_subtotals(segments, total)
    if total is None and summed and len(segments) >= 2:
        # A company's own measure is its segments added up (Brookfield's Q1 2026 10-Q tags each segment's earnings,
        # not their sum).
        total = sum(segments.values())
    if total is None or len(segments) < 2:
        return None

    def name(member: str) -> str:
        own = label(member) or labels.get(member)
        if member in reconciling and local_name(member) in _RECONCILING \
                and (not own or len(own) > 40 or own.lower().startswith("segment")):
            return _RECONCILING[local_name(member)]
        # Palantir's 10-Ks call Government "Government Operating Segment", its 10-Qs "Government": one segment.
        return re.sub(r"\s+operating segment$", "", own, flags=re.I) if own else humanize(member)

    rows = [(member_key(name(member)), name(member), member, value)
            for member, value in sorted(segments.items(), key=lambda item: -item[1])]
    items = [(member_key(name(member)), name(member), member, value) for member, value in reconciling.items()]
    # A tagged item stands only when it closes the gap to operating income: Nvidia tags its corporate item with the
    # segments' own sum, and then the gap is worked out instead.
    gap = total - sum(value for *_rest, value in rows)
    close = max(abs(total) * 0.001, 1.0)
    if items and abs(gap - sum(value for *_rest, value in items)) <= close:
        rows += items
    else:
        # One item may be the gap by itself, beside the segments' own sum tagged as another (Coca-Cola's "Corporate").
        rows += [item for item in items if abs(gap - item[3]) <= close][:1]
    left = total - sum(value for *_rest, value in rows)
    named_lines: set[str] = set()
    if abs(left) > max(abs(total) * 0.001, 1.0):
        closing = _closing_lines(lines, left, line_label)
        rows += closing or [(member_key(_LEFT_OVER), _LEFT_OVER, None, left)]
        named_lines = {key for key, *_rest in closing}
    return IncomeRows(rows, total, named_lines)


def _on_segments(facts: pd.DataFrame, concept: str) -> bool:
    columns = [column for column in facts.columns if column.startswith("dim_") and _axis_name(column) in _SEGMENT_AXES]
    frame = facts[facts["concept"] == concept]
    return bool(columns) and bool(frame[columns].notna().any(axis=1).any())


def _own_concept(facts: pd.DataFrame, pattern: re.Pattern) -> str | None:
    return next((concept for concept in sorted(set(facts["concept"].astype(str))) if pattern.match(concept)
                 and _on_segments(facts, concept)), None)


def segment_income(facts: pd.DataFrame, period_end: date, annual: bool,
                   label: Callable[[str], str | None] = lambda _qname: None,
                   children: dict[str, set[str]] | None = None,
                   line_label: Callable[[str], str | None] | None = None) -> SegmentIncome | None:
    """The period's segment operating income, with the year to date (for a Q4 worked out from the year) and the year
    before (for a recent listing's history); else the company's own segment profit measure."""
    if _on_segments(facts, INCOME_CONCEPT):
        return _measure(facts, INCOME_CONCEPT, GROUP_LABEL, period_end, annual, label, children, line_label=line_label or label)
    concept = _own_concept(facts, _OWN_PROFIT)
    if concept is None:
        contribution = _own_concept(facts, _CONTRIBUTION)
        return _measure(facts, contribution, GROUP_LABEL, period_end, annual, label, children, INCOME_CONCEPT, line_label or label) \
            if contribution else None
    name = "Earnings by Segment" if "Earnings" in concept else "Profit by Segment"
    return _measure(facts, concept, name, period_end, annual, label, children)


def segment_revenue(facts: pd.DataFrame, period_end: date, annual: bool,
                    label: Callable[[str], str | None] = lambda _qname: None,
                    children: dict[str, set[str]] | None = None) -> SegmentIncome | None:
    """A company's own segment revenue measure (Brookfield Asset Management's fee revenues by segment), which its
    segment profit is measured against, beside its GAAP revenue by segment."""
    concept = _own_concept(facts, _OWN_REVENUE)
    return _measure(facts, concept, REVENUE_GROUP_LABEL, period_end, annual, label, children) if concept else None


def _measure(facts: pd.DataFrame, concept: str, name: str, period_end: date, annual: bool,
             label: Callable[[str], str | None], children: dict[str, set[str]] | None,
             reconciled_to: str | None = None,
             line_label: Callable[[str], str | None] | None = None) -> SegmentIncome | None:
    """reconciled_to: the concept whose untagged figure the rows add up to, in place of the measure's own total."""
    selected = facts["concept"] == concept
    if reconciled_to:
        dims = [column for column in facts.columns if column.startswith("dim_")]
        untagged = facts[dims].isna().all(axis=1) if dims else pd.Series(True, index=facts.index)
        selected = (facts["concept"] == concept) & ~untagged | (facts["concept"] == reconciled_to) & untagged
    frame = facts[selected & facts["numeric_value"].notna() & (facts["period_type"] == "duration")]
    own = concept != INCOME_CONCEPT and not reconciled_to
    if frame.empty:
        return None
    labels = _member_labels(facts)
    children = children or {}
    low, high = (330, 400) if annual else (80, 100)
    # Only a reconciliation to operating income has lines to name: a company's own measure stands alone.
    lines = _reconciling_facts(facts, {concept, INCOME_CONCEPT}) if line_label else None

    def rows_of(end: date, first: int, last: int) -> IncomeRows | None:
        return _rows(_period(frame, end, first, last), label, labels, children, own,
                     _period(lines, end, first, last) if lines is not None else None, line_label or label)

    current = rows_of(period_end, low, high)
    if current is None:
        return None
    # A year before, within a week (52/53-week years end on a different day).
    year_ago = period_end - timedelta(days=364)
    prior = next((rows for offset in range(-7, 8) if (rows := rows_of(year_ago + timedelta(days=offset), low, high))), None)
    ytd = None if annual else rows_of(period_end, 250, 290)
    return SegmentIncome(current, ytd, prior, {key: element for key, _name, element, _value in current.rows}, concept, name)
