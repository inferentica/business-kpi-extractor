"""Each segment's operating income, as a report tags it (US filers since ASU 2023-07: OperatingIncomeLoss on the business
segments axis): the business segments only.

Alphabet's segments are Google Services, Google Cloud and Other Bets; its "Alphabet-level activities" (shared AI research,
about −$7.7 billion a quarter in 2026) are not a segment, and neither are Apple's "Corporate" research and development
and general and administrative, or the costs Palantir's reconciliation lists. What a company keeps outside its segments
is not stored (owner, 2026-11-22): the rows are the segments, and they add up to the segments' own operating income, not
the company's. A company's own segment revenue measure keeps the items that close it to its total (Bank of America's
"All Other")."""

from __future__ import annotations

from collections import Counter

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from ..xbrl import _axis_name, _member_labels, humanize, local_name, member_key, remove_subtotals

INCOME_CONCEPT = "us-gaap:OperatingIncomeLoss"
GROUP_KEY = "segment_income"
GROUP_LABEL = "Operating Income by Segment"
_SEGMENT_AXES = {"StatementBusinessSegmentsAxis", "SegmentsAxis"}
_NEUTRAL = {("ConsolidationItemsAxis", "OperatingSegmentsMember"), ("ConsolidatedEntitiesAxis", "OperatingSegmentsMember")}
# Items the consolidation axis tags between the segments and operating income, under the filing's own name when it has
# a short one.
_RECONCILING = {"CorporateNonSegmentMember": "Corporate", "MaterialReconcilingItemsMember": "Reconciling items",
                "SegmentReconcilingItemsMember": "Reconciling items", "IntersegmentEliminationMember": "Eliminations",
                "CorporateReconcilingItemsAndEliminationsMember": "Corporate and eliminations"}
_LEFT_OVER = "Unallocated"
# A company's own segment measures, when it tags no operating income by segment (Brookfield Asset Management's
# "Segment Earnings", its fee-related earnings, over its "Segment Revenue").
_OWN_PROFIT = re.compile(r"^[\w-]+:Segment(?:Earnings|Profit|ProfitLoss|OperatingIncome(?:Loss)?)$")
# A bank's segments are measured on a fully taxable-equivalent basis (Bank of America's "Total revenue, net of interest
# expense (FTE basis)", bac:RevenuesNetOfInterestExpenseFullTaxEquivalentBasis): its segments and "All Other" add up to
# that total, not to GAAP revenue, which is the FTE total less the FTE adjustment.
_OWN_REVENUE = re.compile(r"^[\w-]+:(?:SegmentRevenues?|RevenuesNetOfInterestExpense\w*Tax(?:able)?EquivalentBasis)$")
_TAX_EQUIVALENT = re.compile(r"Tax(?:able)?EquivalentBasis$")
# A company's own segment contribution (Palantir's "pltr:Contribution", each segment's revenue less its own costs, since
# its first inline-XBRL 10-Q): its filing reconciles it to operating income, so the rows do too, with the costs it
# leaves out under the names its reconciliation gives them.
_CONTRIBUTION = re.compile(r"^[\w-]+:(?:Segment)?Contribution$")
# A company's own segment revenue is its revenue by segment: it stands in for the GAAP revenue its notes split by
# segment (Brookfield's leaves out its share of the managers it does not consolidate).
REVENUE_GROUP_KEY = "segments"
REVENUE_GROUP_LABEL = "Revenue by Segment"
LEFT_OVER_KEY = member_key(_LEFT_OVER)


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


def _rows(frame: pd.DataFrame, label: Callable[[str], str | None], labels: dict[str, str],
          children: dict[str, set[str]], summed: bool = False, segments_only: bool = False) -> IncomeRows | None:
    """segments_only: the business segments alone, adding up to their own sum (segment income); else with the items
    that close them to the measure's total (a company's own segment revenue)."""
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

    names = {member: name(member) for member in segments}
    # Two segments under one name would be stored as one: a member whose label repeats another's takes its own element's
    # name (Brookfield's 2026 Q3 10-Q labels its Energy segment's member "Infrastructure").
    shared = Counter(member_key(named) for named in names.values())
    for member, named in names.items():
        if shared[member_key(named)] > 1 and member_key(humanize(member)) != member_key(named):
            names[member] = humanize(member)
    rows = [(member_key(names[member]), names[member], member, value)
            for member, value in sorted(segments.items(), key=lambda item: -item[1])]
    if segments_only:
        return IncomeRows(rows, sum(value for *_rest, value in rows))
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
    if abs(left) > max(abs(total) * 0.001, 1.0):
        rows.append((member_key(_LEFT_OVER), _LEFT_OVER, None, left))
    return IncomeRows(rows, total)


def _on_segments(facts: pd.DataFrame, concept: str) -> bool:
    columns = [column for column in facts.columns if column.startswith("dim_") and _axis_name(column) in _SEGMENT_AXES]
    frame = facts[facts["concept"] == concept]
    return bool(columns) and bool(frame[columns].notna().any(axis=1).any())


def _own_concept(facts: pd.DataFrame, pattern: re.Pattern) -> str | None:
    return next((concept for concept in sorted(set(facts["concept"].astype(str))) if pattern.match(concept)
                 and _on_segments(facts, concept)), None)


def segment_income(facts: pd.DataFrame, period_end: date, annual: bool,
                   label: Callable[[str], str | None] = lambda _qname: None,
                   children: dict[str, set[str]] | None = None) -> SegmentIncome | None:
    """The period's segment operating income, with the year to date (for a Q4 worked out from the year) and the year
    before (for a recent listing's history); else the company's own segment profit measure."""
    if _on_segments(facts, INCOME_CONCEPT):
        return _measure(facts, INCOME_CONCEPT, GROUP_LABEL, period_end, annual, label, children, segments_only=True)
    concept = _own_concept(facts, _OWN_PROFIT)
    if concept is None:
        contribution = _own_concept(facts, _CONTRIBUTION)
        return _measure(facts, contribution, GROUP_LABEL, period_end, annual, label, children, INCOME_CONCEPT, True) \
            if contribution else None
    name = "Earnings by Segment" if "Earnings" in concept else "Profit by Segment"
    return _measure(facts, concept, name, period_end, annual, label, children, segments_only=True)


def segment_revenue(facts: pd.DataFrame, period_end: date, annual: bool,
                    label: Callable[[str], str | None] = lambda _qname: None,
                    children: dict[str, set[str]] | None = None) -> SegmentIncome | None:
    """A company's own segment revenue measure (Brookfield Asset Management's fee revenues by segment), which its
    segment profit is measured against, beside its GAAP revenue by segment."""
    concept = _own_concept(facts, _OWN_REVENUE)
    name = f"{REVENUE_GROUP_LABEL} (FTE basis)" if concept and _TAX_EQUIVALENT.search(concept) else REVENUE_GROUP_LABEL
    return _measure(facts, concept, name, period_end, annual, label, children) if concept else None


def _measure(facts: pd.DataFrame, concept: str, name: str, period_end: date, annual: bool,
             label: Callable[[str], str | None], children: dict[str, set[str]] | None,
             reconciled_to: str | None = None, segments_only: bool = False) -> SegmentIncome | None:
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

    def rows_of(end: date, first: int, last: int) -> IncomeRows | None:
        return _rows(_period(frame, end, first, last), label, labels, children, own, segments_only)

    current = rows_of(period_end, low, high)
    if current is None:
        return None
    # A year before, within a week (52/53-week years end on a different day).
    year_ago = period_end - timedelta(days=364)
    prior = next((rows for offset in range(-7, 8) if (rows := rows_of(year_ago + timedelta(days=offset), low, high))), None)
    ytd = None if annual else rows_of(period_end, 250, 290)
    return SegmentIncome(current, ytd, prior, {key: element for key, _name, element, _value in current.rows}, concept, name)
