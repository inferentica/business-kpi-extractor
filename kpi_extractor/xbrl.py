"""Official revenue breakdowns (segments, products, geography) from a 10-Q/10-K/20-F XBRL instance."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from itertools import combinations
from datetime import date
from typing import Callable

import pandas as pd

REVENUE_CONCEPTS = (
    "us-gaap:Revenues",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap:SalesRevenueNet",
    "us-gaap:RevenuesNetOfInterestExpense",
    "ifrs-full:Revenue",
    "ifrs-full:RevenueFromContractsWithCustomers",
)
# A fact tagged only as "operating segments" is the consolidated total seen from the segment note, not a breakdown.
_NEUTRAL_MEMBERS = {("ConsolidationItemsAxis", "OperatingSegmentsMember")}
# Segments reported before intersegment sales come out (UnitedHealth's Optum sells to UnitedHealthcare) add up to
# revenue only with the eliminations, tagged on the consolidation axis alone; they are kept as one row.
# Each item on the consolidation axis is its own row under the filing's name (JPMorgan's "Corporate", UnitedHealth's
# "Optum Eliminations"); these names stand in for the taxonomy's own long labels.
_CONSOLIDATION_NAMES = {"IntersegmentEliminationMember": "Eliminations", "CorporateNonSegmentMember": "Corporate",
                        "MaterialReconcilingItemsMember": "Reconciling items",
                        "SegmentReconcilingItemsMember": "Reconciling items"}
_SEGMENT_AXES = {"StatementBusinessSegmentsAxis", "SegmentsAxis"}
_PART_AXES = {"ProductOrServiceAxis"}
# Revenue the company reports outside every split (Alphabet's hedging gains and losses, inside its contract-revenue total
# in some years and outside it in others): found when two splits agree on a sum just short of revenue, and kept as one
# row so the split adds up to revenue exactly.
_UNALLOCATED = "unallocated"
MAX_UNALLOCATED = 0.02
# Default titles; each company's KPI list can rename them in its own terms (e.g. "Revenue by Market Platform").
_STANDARD_AXES = {
    "StatementBusinessSegmentsAxis": ("segments", "Revenue by Segment", 0),
    "ProductOrServiceAxis": ("products", "Revenue by Product", 1),
    "StatementGeographicalAxis": ("geography", "Revenue by Geography", 2),
    # IFRS equivalents used by foreign private issuers.
    "SegmentsAxis": ("segments", "Revenue by Segment", 0),
    "ProductsAndServicesAxis": ("products", "Revenue by Product", 1),
    "GeographicalAreasAxis": ("geography", "Revenue by Geography", 2),
}
# Axes that slice revenue by something other than the business (legal entity, scenario, ranges, eliminations).
_IGNORED_AXES = {"ConsolidationItemsAxis", "LegalEntityAxis", "RangeAxis", "StatementScenarioAxis", "RestatementAxis",
                 "RetrospectiveAdjustmentsAxis", "ReclassificationOutOfAccumulatedOtherComprehensiveIncomeAxis",
                 "SubsegmentsConsolidationItemsAxis", "IncomeStatementLocationAxis", "StatementEquityComponentsAxis",
                 # An accounting change's restatement (Broadcom's 2019 Topic 606 columns) and a debt guarantee's
                 # parent-and-subsidiary columns split revenue by bookkeeping, not by business.
                 "AdjustmentsForNewAccountingPronouncementsAxis", "CumulativeEffectPeriodOfAdoptionAxis",
                 "ConsolidatedEntitiesAxis"}
# XBRL amounts are exact to the reporting unit, so a true breakdown adds up to within rounding; a partial one (e.g.
# advertising + other revenue, which leaves out a segment) misses by more.
MAX_RECONCILIATION_ERROR = 0.001


@dataclass
class XbrlGroup:
    key: str
    label: str
    order: int
    members: list[tuple[str, str, float]]  # (member key, label, value)
    total: float | None
    reconciliation_error: float | None
    currency: str | None
    concept: str
    # The same breakdown one year earlier as this filing restates it (member key → value): renamed members (Microsoft's
    # "Gaming" became "Xbox") are recognized by matching these to what the earlier filing reported.
    prior: dict[str, float] = field(default_factory=dict)
    # Member key → its XBRL element ("msft:XBOXMember"): the same element keeps one series when only its label changes.
    elements: dict[str, str] = field(default_factory=dict)
    # A third-quarter 10-Q's nine months to date (member key → value, plus the total), so Q4 is the year less these.
    year_to_date: dict[str, float] = field(default_factory=dict)
    year_to_date_total: float | None = None
    # The year-ago nine months as this filing restates them: last year's Q4 = last year − these, on one basis.
    prior_year_to_date: dict[str, float] = field(default_factory=dict)
    prior_year_to_date_total: float | None = None
    # The other complete split when the axis carries two (members as above); the pipeline keeps the one the
    # company's quarters use.
    alternative: list[tuple[str, str, float]] = field(default_factory=list)
    # Revenue one year earlier as this filing restates it: what a restated year-ago split must add up to.
    prior_total: float | None = None
    # Member key → the segment it is a line of (Alphabet's Search under Google Services, AMD's Client under Client and
    # Gaming), for a split tagged inside one segment and completed by the others: the page nests lines under it.
    parents: dict[str, str] = field(default_factory=dict)
    # The filing's subtotals inside such a split (Alphabet's Google properties = Search + YouTube, Google advertising =
    # properties + Network): (key, label, value), with their year-ago and to-date figures. Kept apart from members, so
    # every sum of a breakdown still counts each line once; parents places them and their lines.
    levels: list[tuple[str, str, float]] = field(default_factory=list)
    level_prior: dict[str, float] = field(default_factory=dict)
    level_year_to_date: dict[str, float] = field(default_factory=dict)


@dataclass
class XbrlCandidate:
    """A breakdown the rules could not reconcile; the AI may choose which of its rows make up revenue."""
    key: str
    label: str
    order: int
    concept: str
    total: float
    currency: str | None
    members: dict[str, tuple[str, float]]  # member key → (label, value)
    prior: dict[str, float] = field(default_factory=dict)
    elements: dict[str, str] = field(default_factory=dict)
    year_to_date: dict[str, float] = field(default_factory=dict)
    year_to_date_total: float | None = None
    prior_year_to_date: dict[str, float] = field(default_factory=dict)
    prior_year_to_date_total: float | None = None

    def group(self, chosen: list[str]) -> XbrlGroup | None:
        """The group formed by the chosen rows, if they add up to revenue; otherwise None."""
        rows = [(key, *self.members[key]) for key in dict.fromkeys(chosen) if key in self.members]
        if len(rows) < 2:
            return None
        error = (sum(value for _key, _label, value in rows) - self.total) / self.total
        if abs(error) > MAX_RECONCILIATION_ERROR:
            return None
        rows.sort(key=lambda row: -row[2])
        keys = {key for key, _label, _value in rows}
        return XbrlGroup(self.key, self.label, self.order, rows, self.total, error, self.currency, self.concept,
                         self.prior, self.elements,
                         {k: v for k, v in self.year_to_date.items() if k in keys}, self.year_to_date_total,
                         self.prior_year_to_date, self.prior_year_to_date_total)


@dataclass
class XbrlBreakdowns:
    period_end: date
    annual: bool
    reported_fiscal_year: str | None
    groups: list[XbrlGroup]
    total_revenue: float | None
    rejected: list[XbrlCandidate] = field(default_factory=list)


def local_name(qname: str) -> str:
    return qname.split(":", 1)[-1] if qname else qname


def _axis_name(column: str) -> str:
    """ "dim_us-gaap_StatementBusinessSegmentsAxis" → "StatementBusinessSegmentsAxis"."""
    match = re.match(r"^dim_[^_]+_(.+)$", column)
    return match.group(1) if match else column[4:]


def humanize(name: str) -> str:
    """ "ChinaIncludingHongKongMember" → "China Including Hong Kong"."""
    base = re.sub(r"(SegmentMember|Member|Axis)$", "", local_name(name))
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", base)
    return words.strip() or base


def official_labels(element_catalog: dict | None) -> Callable[[str], str | None]:
    """The filing's own label for an axis or member ("Americas" for aapl:AmericasSegmentMember), terse label first."""
    catalog = element_catalog or {}

    def label(qname: str) -> str | None:
        element = catalog.get(qname.replace(":", "_"))
        labels = getattr(element, "labels", None) or {}
        for role, text in sorted(labels.items(), key=lambda item: (not item[0].endswith("terseLabel"), not item[0].endswith("/label"))):
            if role.endswith(("terseLabel", "/label")) and text:
                cleaned = re.sub(r"\s*\[(member|axis|domain|line items|table)\]\s*$", "", str(text), flags=re.I).strip()
                if cleaned:
                    return cleaned
        return None

    return label


def member_children(xbrl) -> dict[str, set[str]]:
    """Each member's declared children (unh:TotalOptumMember → Optum Health, Optum Insight, Optum Rx), from the filing's
    presentation trees and its definition domains: what the company says a member is made of."""
    children: dict[str, set[str]] = {}

    def qname(element_id: str) -> str:
        return element_id.replace("_", ":", 1)

    for tree in (getattr(getattr(xbrl, "parser", None), "presentation_trees", None) or {}).values():
        for node_id, node in tree.all_nodes.items():
            kids = [child for child in node.children if child.endswith("Member")]
            if node_id.endswith("Member") and kids:
                children.setdefault(qname(node_id), set()).update(qname(child) for child in kids)
    for domains in (getattr(xbrl, "domains_by_role", None) or {}).values():
        for element_id, domain in domains.items():
            kids = [member for member in domain.members if member.endswith("Member")]
            if element_id.endswith("Member") and kids:
                children.setdefault(qname(element_id), set()).update(qname(member) for member in kids)
    return children


def _descendants(member: str, children: dict[str, set[str]]) -> set[str]:
    found, stack = set(), list(children.get(member, ()))
    while stack:
        child = stack.pop()
        if child not in found:
            found.add(child)
            stack.extend(children.get(child, ()))
    return found


def drop_declared_parents(members: dict[str, float], total: float | None, children: dict[str, set[str]]) -> dict[str, float]:
    """Members the filing declares as parents of at least two members also present (UnitedHealth's "Total Optum" over
    its three Optum segments, net of their internal sales so not their sum) are subtotals, not parts. Parents that
    together make a complete split of revenue (Product and Service beside the product lines) are a coarser split and
    stay, for the two-split logic to keep apart."""
    parents = {member for member in members if len(_descendants(member, children) & set(members)) >= 2}
    if not parents:
        return members
    if total and len(parents) >= 2 and abs(sum(members[p] for p in parents) - total) <= abs(total) * MAX_RECONCILIATION_ERROR:
        return members
    return {member: value for member, value in members.items() if member not in parents}


def extract_breakdowns(facts: pd.DataFrame, entity: dict, form: str,
                       label: Callable[[str], str | None] = lambda _qname: None,
                       children: dict[str, set[str]] | None = None) -> XbrlBreakdowns | None:
    period_end = _as_date(entity.get("document_period_end_date"))
    if period_end is None:
        return None
    annual = form.upper().startswith(("10-K", "20-F", "40-F"))
    frame = facts[facts["concept"].isin(REVENUE_CONCEPTS) & (facts["period_type"] == "duration")].copy()
    if frame.empty:
        return XbrlBreakdowns(period_end, annual, _fiscal_year(entity), [], None)
    frame["_start"] = pd.to_datetime(frame["period_start"], errors="coerce")
    frame["_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    days = (frame["_end"] - frame["_start"]).dt.days
    duration = days.between(330, 400) if annual else days.between(80, 100)
    prior_end = frame["_end"].dt.date.map(lambda end: end is not None and not pd.isna(end) and 358 <= (period_end - end).days <= 372)
    prior_frame = frame[duration & prior_end & frame["numeric_value"].notna()]
    ytd_frame = frame[(frame["_end"].dt.date == period_end) & days.between(250, 290) & frame["numeric_value"].notna()]
    prior_ytd_frame = frame[prior_end & days.between(250, 290) & frame["numeric_value"].notna()]
    in_period = (frame["_end"].dt.date == period_end) & duration
    frame = frame[in_period & frame["numeric_value"].notna()]
    dimension_columns = [column for column in frame.columns if column.startswith("dim_")]
    axis_qnames = {_axis_name(column): column[4:].replace("_", ":", 1) for column in dimension_columns}
    labels = _member_labels(facts)

    # Facts tagged "operating segments" on the consolidation axis are the segments' own measure: alone, they are the
    # consolidated figures (a filer that tags every line so); beside consolidated facts of the same concept and axis,
    # they are another table (KKR's segment revenues beside its GAAP fees), read only where no consolidated one is.
    pure_totals: dict[str, float] = {}
    segment_totals: dict[str, float] = {}
    currencies: dict[str, str] = {}
    tagged: list[tuple[str, dict[str, str], float, bool]] = []
    for _, fact in frame.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        kept = {axis: member for axis, member in dimensions.items() if (axis, local_name(member)) not in _NEUTRAL_MEMBERS}
        segment_level = len(kept) != len(dimensions)
        dimensions = kept
        concept = fact["concept"]
        value = float(fact["numeric_value"])
        currency = fact.get("currency") or fact.get("unit_ref")
        if isinstance(currency, str) and currency:
            currencies[concept] = currency.upper().replace("ISO4217:", "")
        if not dimensions:
            (segment_totals if segment_level else pure_totals).setdefault(concept, value)
        else:
            tagged.append((concept, dimensions, value, segment_level))
    totals = {**segment_totals, **pure_totals}

    # Candidate breakdowns per axis: facts tagged with that axis alone, and facts tagged with it plus an axis that only
    # ever takes one value alongside it (Netflix tags every region "Streaming"), which then adds nothing.
    candidates: dict[str, list[tuple[str, dict[str, float]]]] = {}
    single: dict[tuple[str, str], dict[str, float]] = {}
    paired: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    single_by_level: dict[bool, dict[tuple[str, str], dict[str, float]]] = {False: {}, True: {}}
    paired_levels: dict[tuple[str, frozenset], set[bool]] = {}
    paired_pure: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    paired_segment: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    # (axis, concept) → the one (axis, member) its facts are also tagged with (Alphabet's products, all "Google
    # Services"), so the year-ago and to-date figures are read from facts tagged the same way.
    fixed_for: dict[tuple[str, str], tuple[str, str]] = {}
    eliminations: dict[str, dict[str, float]] = {}
    for concept, dimensions, value, level in tagged:
        if set(dimensions) == {"ConsolidationItemsAxis"} and value:
            items = eliminations.setdefault(concept, {})
            items[dimensions["ConsolidationItemsAxis"]] = items.get(dimensions["ConsolidationItemsAxis"], 0.0) + value
        if any(axis in _IGNORED_AXES for axis in dimensions):
            continue
        if len(dimensions) == 1:
            axis, member = next(iter(dimensions.items()))
            single.setdefault((concept, axis), {}).setdefault(member, value)
            single_by_level[level].setdefault((concept, axis), {}).setdefault(member, value)
        elif len(dimensions) == 2:
            paired.setdefault((concept, frozenset(dimensions)), []).append((dimensions, value))
            paired_levels.setdefault((concept, frozenset(dimensions)), set()).add(level)
            (paired_segment if level else paired_pure).setdefault((concept, frozenset(dimensions)), []).append((dimensions, value))
    # The segments' own measure on an axis no consolidated fact uses (KKR's 2018 business lines) is checked against
    # its own total; consolidated members that add up to their own total stand alone, without the segment-level facts
    # beside them (KKR's GAAP fees beside its segment revenues).
    segment_level: set[tuple[str, str]] = set()
    pure_only: set[tuple[str, str]] = set()
    part_of: dict[tuple[str, str], float] = {}
    for key in list(single):
        pure, own = single_by_level[False].get(key), single_by_level[True].get(key)
        if own and not pure:
            segment_level.add(key)
            continue
        if not (pure and own):
            continue
        if key[1] in _SEGMENT_AXES:
            # The segments' own measure over the GAAP split of revenue by segment, when it covers every segment and
            # adds up to its own total (Apollo's Principal Investing, which earns no GAAP revenue); as a company's own
            # segment revenue is its revenue by segment.
            whole = segment_totals.get(key[0])
            leaves = drop_overlaps(remove_subtotals(own, whole), whole) if whole else {}
            if whole and len(leaves) >= len(pure) and abs(sum(leaves.values()) - whole) <= abs(whole) * MAX_RECONCILIATION_ERROR:
                single[key] = own
                segment_level.add(key)
            continue
        # The consolidated lines alone, when they share members with the segments' own (KKR's fees: each line
        # consolidated and again by segment, two tables) or add up exactly to the concept's figure for one segment
        # (KKR's fees since 2021: Fees and Other of its Asset Management segment, its insurer's revenue beside it),
        # which is then their total; else one split (UnitedHealth's segments beside one consolidated line).
        figures = {**single_by_level[True].get((key[0], "StatementBusinessSegmentsAxis"), {}),
                   **single_by_level[False].get((key[0], "StatementBusinessSegmentsAxis"), {})}
        whole = sum(pure.values())
        part = next((value for value in figures.values() if value and abs(whole - value) <= max(abs(value) * 1e-6, 0.5)), None)
        if part is not None or set(pure) & set(own):
            single[key] = pure
            pure_only.add(key)
            if part is not None:
                part_of[key] = part
    for key, levels in paired_levels.items():
        if levels == {True}:
            segment_level.update((key[0], axis) for axis in key[1] if (key[0], axis) not in single_by_level[False])

    def total_for(concept: str, axis: str | None = None) -> float | None:
        if (concept, axis) in part_of:
            return part_of[(concept, axis)]
        if (concept, axis) in segment_level and segment_totals.get(concept):
            return segment_totals[concept]
        among = pure_totals if (concept, axis) in pure_only else totals
        return among.get(concept) or next((among[other] for other in REVENUE_CONCEPTS if other in among), None)

    # A member tagged only beside the one member of another axis it belongs to (Intel's 2021 "DCG platform", only with
    # its Data Center Group) joins the axis's members with that figure; one tagged beside several is never added up.
    beside: dict[tuple[str, str], dict[str, list[float]]] = {}
    for (concept, _axes), items in paired.items():
        for dimensions, value in items:
            for axis, member in dimensions.items():
                beside.setdefault((concept, axis), {}).setdefault(member, []).append(value)
    def adds_up(rows: dict[str, float], total: float | None) -> bool:
        leaves = drop_overlaps(remove_subtotals(rows, total), total)
        return bool(total) and abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR

    for (concept, axis), members in single.items():
        extra = {member: values[0] for member, values in beside.get((concept, axis), {}).items()
                 if member not in members and len(values) == 1}
        # Only when the axis's own members fall short of revenue and the extra ones complete it.
        if extra and not adds_up(members, total_for(concept, axis)) and adds_up({**members, **extra}, total_for(concept, axis)):
            members = {**members, **extra}
        candidates.setdefault(axis, []).append((concept, members))
    for (concept, axes), items in paired.items():
        found = False
        # Consolidated lines tagged under one segment that add up exactly to its figure (KKR's fees since 2021: Fees
        # and Other of its Asset Management segment, beside segment-level figures under another member): a split of
        # that figure, with it as the total.
        segment_axis = next((axis for axis in axes if axis in _SEGMENT_AXES), None)
        pure_items = paired_pure.get((concept, axes), [])
        # Only beside segment-level figures: lines of one segment alone (Alphabet's Google Services) are completed by
        # the other segments instead.
        if segment_axis and pure_items and paired_levels.get((concept, axes)) == {False, True}:
            other = next(axis for axis in axes if axis != segment_axis)
            split = None
            # Each segment's consolidated lines on their own (KKR's Q1 2024 tags two stray lines under another member).
            for member in sorted({dimensions[segment_axis] for dimensions, _v in pure_items}):
                lines: dict[str, float] = {}
                for dimensions, value in pure_items:
                    if dimensions[segment_axis] == member:
                        lines.setdefault(dimensions[other], value)
                figure = single.get((concept, segment_axis), {}).get(member)
                # A line beside its segment-level twin (KKR's "Investment": −$277 million against the segments' +$277
                # million in 2022, the same $198 million in 2023) is no part of the split.
                twins = {key for key in lines if any(d[other] == key for d, _v in paired_segment.get((concept, axes), []))}
                if figure and abs(sum(lines.values()) - figure) > max(abs(figure) * 1e-6, 0.5) and twins:
                    lines = {key: value for key, value in lines.items() if key not in twins}
                if figure and len(lines) >= 2 and abs(sum(lines.values()) - figure) <= max(abs(figure) * 1e-6, 0.5):
                    split = (member, lines, figure)
                    break
            if split:
                member, lines, figure = split
                candidates.setdefault(other, []).append((concept, lines))
                fixed_for.setdefault((other, concept), (segment_axis, member))
                part_of[(concept, other)] = figure
                pure_only.add((concept, other))
                continue
        for fixed in axes:
            if len({dimensions[fixed] for dimensions, _value in items}) != 1:
                continue
            other = next(axis for axis in axes if axis != fixed)
            members: dict[str, float] = {}
            for dimensions, value in items:
                members.setdefault(dimensions[other], value)
            candidates.setdefault(other, []).append((concept, members))
            fixed_for.setdefault((other, concept), (fixed, items[0][0][fixed]))
            found = True
        # Products tagged under segments where the filer typed one segment wrong (AMD's FY2025 10-K files Gaming under a
        # "Gaming" segment, Client under "Client and Gaming"): the product lines alone, which _completed_by_segments
        # keeps only when they add up exactly to one real segment.
        part = next((axis for axis in axes if axis in _PART_AXES), None)
        if not found and part and any(axis in _SEGMENT_AXES for axis in axes):
            members = {}
            for dimensions, value in items:
                members.setdefault(dimensions[part], value)
            if len(members) == len(items):
                candidates.setdefault(part, []).append((concept, members))

    def reconciled(axis: str, concept: str, members: dict[str, float]) -> dict[str, float]:
        """A segment split with its eliminations as one more row, when only that makes it add up to revenue."""
        total = total_for(concept, axis)
        members = drop_declared_parents(members, total, children or {})
        if axis not in _SEGMENT_AXES or not total or not eliminations.get(concept):
            return members
        def adds_up(rows: dict[str, float], overlaps: bool = True) -> bool:
            leaves = remove_subtotals(rows, total)
            leaves = drop_overlaps(leaves, total) if overlaps else leaves
            return abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR
        with_eliminations = {**members, **eliminations[concept]}
        # Every segment with the eliminations beats the segments adding up only once one is dropped: Intel Foundry's
        # revenue is nearly all intersegment, so without it the others came within 0.1% of revenue.
        if not adds_up(members, overlaps=False) and adds_up(with_eliminations, overlaps=False):
            return with_eliminations
        return with_eliminations if not adds_up(members) and adds_up(with_eliminations) else members

    def score(candidate: tuple[str, dict[str, float]], axis: str = ""):
        concept, members = candidate
        members = reconciled(axis, concept, members)
        total = total_for(concept, axis)
        leaves = drop_overlaps(remove_subtotals(members, total), total)
        reconciles = bool(total) and abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR
        # Lines with a figure: a line tagged at zero (KKR's Principal Activities fees) is no part of the split.
        return (reconciles, sum(1 for value in leaves.values() if value), concept in totals)

    best_by_axis = {axis: max(options, key=lambda option, axis=axis: score(option, axis)) for axis, options in candidates.items()}

    def leaf_sum(axis: str, concept: str, members: dict[str, float]) -> float:
        total = total_for(concept, axis)
        return sum(drop_overlaps(remove_subtotals(reconciled(axis, concept, members), total), total).values())

    sums_by_concept: dict[str, list[float]] = {}
    for axis, (concept, members) in best_by_axis.items():
        if len(members) >= 2 and total_for(concept, axis):
            sums_by_concept.setdefault(concept, []).append(leaf_sum(axis, concept, members))

    def unallocated(axis: str, concept: str, members: dict[str, float]) -> float | None:
        """Revenue outside the split, when the split falls short and another split of the same figure agrees."""
        total = total_for(concept, axis)
        if not total:
            return None
        own = leaf_sum(axis, concept, members)
        gap = total - own
        # Recorded whatever its size, so every period of the series carries it and its quarters add up to its year.
        if abs(gap) <= abs(total) * 1e-9 or abs(gap) > abs(total) * MAX_UNALLOCATED:
            return None
        agreeing = [other for other in sums_by_concept.get(concept, []) if abs(other - own) <= abs(own) * 1e-5]
        return gap if len(agreeing) >= 2 else None  # itself and at least one other split

    def frame_total(source: pd.DataFrame, concept: str, level: bool = False) -> float | None:
        if not dimension_columns:
            return None
        if level and (own := _plain_total(source, concept, dimension_columns, True)) is not None:
            return own
        plain = source[source[dimension_columns].isna().all(axis=1)]
        for name in (concept, *REVENUE_CONCEPTS):
            values = plain[plain["concept"] == name]["numeric_value"]
            if len(values):
                return float(values.iloc[0])
        return None

    # Plain figures of the period that sit beside revenue (Alphabet's "Hedging gains (losses)", tagged
    # RevenueNotFromContractWithCustomer): what a gap the splits leave is, when one of them matches it.
    plain = _plain_revenue_figures(facts, period_end, annual, label)

    def outside_name(gap: float) -> str | None:
        return next((name for value, name in plain if abs(value - gap) <= max(abs(gap) * 1e-6, 0.5)), None)

    groups: list[XbrlGroup] = []
    rejected: list[XbrlCandidate] = []
    for axis, (concept, members) in best_by_axis.items():
        outside_label: str | None = None
        if len(members) < 2:
            continue
        # Every breakdown must add up to reported revenue; one that cannot be checked is not kept.
        total = total_for(concept, axis)
        if not total:
            continue
        standard = _STANDARD_AXES.get(axis)
        axis_label = label(axis_qnames.get(axis, axis)) or humanize(axis)
        own_level = True if (concept, axis) in segment_level else False if (concept, axis) in pure_only else None
        # A company's own axis tagged "operating segments" is its segments (KKR's 2018 business lines, its segments
        # before and after), named without the axis's words ("Private Markets Business Line" is Private Markets).
        suffix = None
        if standard is None and own_level and not any(other in _SEGMENT_AXES for other in best_by_axis):
            standard, suffix = _STANDARD_AXES["StatementBusinessSegmentsAxis"], _title(axis_label)
        key, title, order = standard or (f"x_{_snake(axis)}", f"Revenue by {_title(axis_label)}", 10)

        def bare(name: str) -> str:
            return name[:-len(suffix)].rstrip(" -–,") if suffix and name.lower().endswith(suffix.lower()) and len(name) > len(suffix) else name

        def consolidation_name(member: str) -> str | None:
            """The filing's own name for a consolidation item, unless it only has the taxonomy's long one."""
            if local_name(member) not in _CONSOLIDATION_NAMES and member not in eliminations.get(concept, {}):
                return None
            own = label(member)
            if own and "reconciling" in own.lower():
                return "Reconciling items"  # one series whether a year's filing says "Segment Reconciling Items" or not
            if own and len(own) <= 40 and not own.lower().startswith("segment reporting"):
                return own
            return _CONSOLIDATION_NAMES.get(local_name(member)) or own

        def named(rows: dict[str, float]) -> list[tuple[str, str, float]]:
            out = []
            for member, value in sorted(rows.items(), key=lambda item: -item[1]):
                name = (outside_label or "Unallocated") if member == _UNALLOCATED else consolidation_name(member) \
                    or label(member) or labels.get(member) or humanize(member)
                name = bare(name)
                out.append((member_key(name), name, value))
            return out

        def elements(rows: dict[str, float]) -> dict[str, str]:
            return {member_key(bare(consolidation_name(member) or label(member) or labels.get(member) or humanize(member))): member
                    for member in rows}

        outside = unallocated(axis, concept, members)
        outside_label = outside_name(outside) if outside is not None else None
        members = reconciled(axis, concept, members)
        consolidation = [member for member in members if member in eliminations.get(concept, {})]
        if outside is not None:
            leaf_members = set(drop_overlaps(remove_subtotals(members, total), total))
            members = {**members, _UNALLOCATED: outside}

        def frame_members(source: pd.DataFrame) -> dict[str, float]:
            rows = _single_axis_members(source, concept, axis, dimension_columns, fixed_for.get((axis, concept)), own_level)
            if consolidation and rows:
                items = _consolidation_items(source, concept, dimension_columns)
                rows.update({member: items[member] for member in consolidation if member in items})
            if outside is not None and rows and (whole := frame_total(source, concept, own_level)) is not None:
                rows[_UNALLOCATED] = whole - sum(value for member, value in rows.items() if member in leaf_members)
            return rows

        fine, coarse = _two_partitions(members, total)
        leaves = drop_overlaps(remove_subtotals(fine, total), total)
        error = (sum(leaves.values()) - total) / total if len(leaves) >= 2 else None
        prior = _by_key(named(frame_members(prior_frame)))
        ytd = _by_key(named(frame_members(ytd_frame)))
        ytd_total = _plain_total(ytd_frame, concept, dimension_columns, own_level) if dimension_columns else None
        if outside is not None:
            ytd_total = frame_total(ytd_frame, concept, own_level)
        prior_ytd = _by_key(named(frame_members(prior_ytd_frame)))
        prior_ytd_total = _plain_total(prior_ytd_frame, concept, dimension_columns, own_level) if dimension_columns else None
        if outside is not None:
            prior_ytd_total = frame_total(prior_ytd_frame, concept, own_level)
        prior_total = frame_total(prior_frame, concept, own_level)
        if error is not None and abs(error) <= MAX_RECONCILIATION_ERROR:
            # Products against services (Amazon, Microsoft) is a view of its own beside the product lines, not a
            # coarser version of them: both are published, so neither depends on the AI listing it.
            split = coarse and all(_PRODUCT_OR_SERVICE.match(key) for key, _name, _value in named(coarse))
            # A row of nothing all year (Broadcom's 2019 "Intercompany revenue") is not a part of revenue; a business
            # at zero this quarter but not to date (Nvidia's licensing after its Q1) stays, so its year adds up.
            kept = {key for key, _name, _value in named({member: 1.0 for member in consolidation})}
            rows = [row for row in named(leaves) if row[2] != 0 or ytd.get(row[0]) or prior.get(row[0]) or row[0] in kept]
            groups.append(XbrlGroup(key, title, order, rows, total, error, currencies.get(concept), concept,
                                    prior, {**elements(coarse), **elements(leaves)}, ytd, ytd_total, prior_ytd,
                                    prior_ytd_total, [] if split or not coarse else named(coarse), prior_total))
            if split:
                groups.append(XbrlGroup(
                    "product_service", "Revenue by Product and Service", order, named(coarse), total,
                    (sum(coarse.values()) - total) / total, currencies.get(concept), concept, prior, elements(coarse),
                    ytd, ytd_total, prior_ytd, prior_ytd_total, prior_total=prior_total))
            continue
        # Worth an AI review only when it could plausibly be a breakdown: a standard axis, or a custom one whose rows
        # are of the right order of magnitude.
        positive = sum(value for value in members.values() if value > 0)
        # Two lines may still be one segment's split (AMD's Client and Gaming, by product since 2025): kept to be completed
        # by the other segments below, never sent for review alone.
        if 2 <= len(members) <= 40 and (standard or 0.5 * total <= positive <= 3 * total or len(members) == 2):
            rejected.append(XbrlCandidate(key, title, order, concept, total, currencies.get(concept),
                                          {k: (n, v) for k, n, v in named(members)}, prior, elements(members),
                                          ytd, ytd_total, prior_ytd, prior_ytd_total))
    for candidate in list(rejected):
        completed = _completed_by_segments(candidate, groups)
        if completed is not None:
            groups.append(completed)
            rejected.remove(candidate)
    rejected = [candidate for candidate in rejected if len(candidate.members) >= 3]
    groups.sort(key=lambda group: (group.order, group.key))
    _name_regional_views(groups)
    total_revenue = next((totals[concept] for concept in REVENUE_CONCEPTS if concept in totals), None)
    return XbrlBreakdowns(period_end, annual, _fiscal_year(entity), groups, total_revenue, rejected)


# Words that name a place: a company whose every segment is one (Apple's Americas, Europe, Greater China, Japan and Rest
# of Asia Pacific) reports by region.
_PLACE = re.compile(r"\b(americas?|north|south|latin|europe|european|emea|apac|asia|pacific|china|japan|india|africa|"
                    r"middle east|international|united states|u\.s\.|us|canada|mexico|brazil|korea|taiwan|germany|"
                    r"united kingdom|uk|france|rest of world|other countries|countries|regions?|greater)\b", re.IGNORECASE)


def _name_regional_views(groups: list[XbrlGroup]) -> None:
    """Segments that are regions read as "Revenue by Region", and the country split beside them as "Revenue by Country",
    so two geographic views never pass as one by segment and one by geography."""
    segments = next((group for group in groups if group.key == "segments"), None)
    if segments is None or not segments.members or not all(_PLACE.search(name) for _key, name, _value in segments.members):
        return
    segments.label = "Revenue by Region"
    for group in groups:
        if group.key == "geography":
            group.label = "Revenue by Country"


def _completed_by_segments(candidate: XbrlCandidate, groups: list[XbrlGroup]) -> XbrlGroup | None:
    """A split tagged inside one segment only (Alphabet's Search, YouTube, Network and subscriptions add up to Google
    Services, while Cloud and Other Bets appear only as segments), completed by the company's other segments and its
    balancing rows, when its lines add up exactly to that segment and the whole then adds up to revenue."""
    segments = next((group for group in groups if group.key == "segments"), None)
    if segments is None or candidate.key == "segments" or any(group.key == candidate.key for group in groups):
        return None
    rows = {key: value for key, (_label, value) in candidate.members.items()}
    for key, label, part in segments.members:
        if not part:
            continue
        leaves = drop_overlaps(remove_subtotals(rows, part), part)
        if len(leaves) < 2 or abs(sum(leaves.values()) - part) > abs(part) * 1e-5:
            continue
        others = [(k, n, v) for k, n, v in segments.members if k != key]
        if any(k in leaves for k, _n, _v in others):
            return None  # a line and a segment under one name: not one split
        members = [(k, candidate.members[k][0], v) for k, v in sorted(leaves.items(), key=lambda item: -item[1])] + others
        error = (sum(v for _k, _n, v in members) - segments.total) / segments.total if segments.total else None
        if error is None or abs(error) > MAX_RECONCILIATION_ERROR:
            return None
        def merged(lines: dict[str, float], rest: dict[str, float]) -> dict[str, float]:
            return {**{k: v for k, v in lines.items() if k in leaves}, **{k: v for k, v in rest.items() if k != key}}
        parents, subtotals = subtotal_tree(rows, leaves, key)
        return XbrlGroup(candidate.key, candidate.label, candidate.order, members, segments.total, error,
                         segments.currency, candidate.concept, merged(candidate.prior, segments.prior),
                         {**segments.elements, **candidate.elements},
                         merged(candidate.year_to_date, segments.year_to_date), segments.year_to_date_total,
                         merged(candidate.prior_year_to_date, segments.prior_year_to_date), segments.prior_year_to_date_total,
                         prior_total=segments.prior_total, parents=parents,
                         levels=[(k, candidate.members[k][0], v) for k, v in sorted(subtotals.items(), key=lambda item: -item[1])],
                         level_prior={k: v for k, v in candidate.prior.items() if k in subtotals},
                         level_year_to_date={k: v for k, v in candidate.year_to_date.items() if k in subtotals})
    return None


def subtotal_tree(rows: dict[str, float], leaves: dict[str, float], segment: str) -> tuple[dict[str, str], dict[str, float]]:
    """Inside one segment's split: each line's parent, the smallest subtotal the filing tags that holds it (Search and
    YouTube in Google properties; it and Network in Google advertising), else the segment; and those subtotals."""
    positive = [key for key, value in leaves.items() if value > 0]
    covers: dict[str, frozenset] = {}
    for key, value in rows.items():
        if key in leaves or value <= 0:
            continue
        found = _leaves_summing_to(leaves, positive, value)
        if found and len(found) < len(positive):
            covers[key] = found
    sets = {**{key: frozenset([key]) for key in leaves}, **covers}
    parents: dict[str, str] = {}
    for key, held in sets.items():
        holders = [other for other, cover in covers.items() if other != key and held < cover]
        parents[key] = min(holders, key=lambda other: rows[other]) if holders else segment
    return parents, {key: rows[key] for key in covers}


def _leaves_summing_to(leaves: dict[str, float], keys: list[str], target: float) -> frozenset | None:
    for size in range(2, min(len(keys), 10) + 1):
        for combo in combinations(keys, size):
            if abs(sum(leaves[key] for key in combo) - target) <= abs(target) * 1e-5:
                return frozenset(combo)
    return None


def _by_key(rows: list[tuple[str, str, float]]) -> dict[str, float]:
    """Values by member key, adding rows whose labels share a key ("Other" and "All other") instead of keeping one."""
    out: dict[str, float] = {}
    for key, _label, value in rows:
        out[key] = out.get(key, 0.0) + value
    return out


def _consolidation_items(frame: pd.DataFrame, concept: str, dimension_columns: list[str]) -> dict[str, float]:
    """A concept's facts tagged on the consolidation axis alone, other than its operating segments, by member
    (intersegment eliminations, corporate items, reconciling items)."""
    items: dict[str, float] = {}
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        if set(dimensions) == {"ConsolidationItemsAxis"} and local_name(dimensions["ConsolidationItemsAxis"]) != "OperatingSegmentsMember":
            member = dimensions["ConsolidationItemsAxis"]
            items[member] = items.get(member, 0.0) + float(fact["numeric_value"])
    return items


def _plain_revenue_figures(facts: pd.DataFrame, period_end, annual: bool, label: Callable[[str], str | None]) -> list[tuple[float, str]]:
    """Undimensioned revenue-like figures of the period other than revenue itself, with the filing's own names."""
    source = facts[(facts["period_type"] == "duration") & facts["numeric_value"].notna()]
    source = source[source["concept"].astype(str).str.contains("Revenue") & ~source["concept"].isin(REVENUE_CONCEPTS)]
    if source.empty:
        return []
    start = pd.to_datetime(source["period_start"], errors="coerce")
    end = pd.to_datetime(source["period_end"], errors="coerce")
    days = (end - start).dt.days
    keep = (end.dt.date == period_end) & (days.between(330, 400) if annual else days.between(80, 100))
    dimensions = [column for column in source.columns if column.startswith("dim_")]
    if dimensions:
        keep &= source[dimensions].isna().all(axis=1)
    figures = []
    for _, fact in source[keep].iterrows():
        own = fact.get("label")
        name = own if isinstance(own, str) and own.strip() else label(str(fact["concept"])) or humanize(str(fact["concept"]))
        figures.append((float(fact["numeric_value"]), name.strip()))
    return figures


def _single_axis_members(frame: pd.DataFrame, concept: str, axis: str, dimension_columns: list[str],
                         fixed: tuple[str, str] | None = None, segment_level: bool | None = None) -> dict[str, float]:
    """Members of one axis for one concept, from facts tagged with that axis alone, or with it and the one member of
    another axis its period's facts all carry (fixed); only the consolidated facts (segment_level False), only the
    segments' own (True), or both (None)."""
    members: dict[str, float] = {}
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        kept = {a: m for a, m in dimensions.items() if (a, local_name(m)) not in _NEUTRAL_MEMBERS}
        if (segment_level is not None and (len(kept) != len(dimensions)) != segment_level) or axis not in kept:
            continue
        if len(kept) == 1 or (fixed and len(kept) == 2 and kept.get(fixed[0]) == fixed[1]):
            members.setdefault(kept[axis], float(fact["numeric_value"]))
    return members


def _plain_total(frame: pd.DataFrame, concept: str, dimension_columns: list[str], segment_level: bool | None = False) -> float | None:
    """The concept's figure with no axis but, for the segments' own measure, "operating segments"."""
    rows = frame[frame["concept"] == concept]
    for _, fact in rows.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        if all((a, local_name(m)) in _NEUTRAL_MEMBERS for a, m in dimensions.items()) and bool(dimensions) == bool(segment_level):
            return float(fact["numeric_value"])
    return None


_PRODUCT_OR_SERVICE = re.compile(r"^(?:net)?(?:products?|services?(?:andother)?)(?:sales|revenues?)?$")


def _two_partitions(members: dict[str, float], total: float | None) -> tuple[dict[str, float], dict[str, float]]:
    """(finer, coarser) when one axis carries two complete splits of revenue (Microsoft tags Product / Service and
    other beside its product lines; Broadcom's 10-K tags countries beside the regions its 10-Qs use), else (members, {})."""
    if not total or len(members) < 4:
        return members, {}
    tolerance = abs(total) * MAX_RECONCILIATION_ERROR
    keys = list(members)
    if abs(sum(members.values()) - 2 * total) > 2 * tolerance:
        # Regions beside the countries they are made of (Adobe's 10-K tags Americas beside United States and Other
        # Americas, Asia beside Japan and Other Asia; its 10-Qs give the regions only): the split with the regions is
        # the coarser one.
        parents = [key for key in keys if members[key] > 0 and any(
            abs(sum(members[other] for other in combo) - members[key]) <= tolerance
            for size in (2, 3) for combo in combinations([other for other in keys if other != key], size))]
        leaves = [key for key in keys if key not in parents]
        if parents and abs(sum(members[key] for key in leaves) - total) <= tolerance:
            used: set[str] = set()
            for parent in sorted(parents, key=lambda key: -members[key]):
                combo = next((combo for size in (2, 3) for combo in combinations([key for key in leaves if key not in used], size)
                              if abs(sum(members[key] for key in combo) - members[parent]) <= tolerance), None)
                if combo is None:
                    return members, {}
                used.update(combo)
            coarse = {key: value for key, value in members.items() if key not in used}
            if len(coarse) >= 2 and abs(sum(coarse.values()) - total) <= tolerance:
                return {key: members[key] for key in leaves}, coarse
        return members, {}
    for size in (2, 3, 4):
        for combo in combinations(keys, size):
            if len(keys) - size >= size and abs(sum(members[key] for key in combo) - total) <= tolerance:
                coarse = {key: members[key] for key in combo}
                return {key: value for key, value in members.items() if key not in combo}, coarse
    return members, {}


def drop_overlaps(members: dict[str, float], total: float | None) -> dict[str, float]:
    """Drops up to two detail rows that overlap others (Meta lists "U.S." beside "US & Canada") when, without them, the
    breakdown adds up to reported revenue and with them it does not."""
    if not total or len(members) < 3:
        return members
    tolerance = abs(total) * MAX_RECONCILIATION_ERROR
    excess = sum(members.values()) - total
    if abs(excess) <= tolerance:
        return members
    keys = list(members)
    for size in (1, 2):
        for combo in combinations(keys, size):
            if len(keys) - size >= 2 and abs(excess - sum(members[key] for key in combo)) <= tolerance:
                return {key: value for key, value in members.items() if key not in combo}
    return members


_RESIDUAL = re.compile(r"^(?:all)?others?(?:countries|country|regions?|areas?|products?|segments?)?(?:notseparatelydisclosed)?$")


def member_key(label: str) -> str:
    """A member's identity across filings: its label normalized, so a 10-K's "ComputeAndNetworkingSegmentMember"
    and a 10-Q's "ComputeAndNetworkingMember" (both "Compute & Networking") are one series, as are "Other countries"
    and "All other countries not separately disclosed"."""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", label) or label  # "Asia-Pacific (APAC)" is "Asia-Pacific"
    key = re.sub(r"[^a-z0-9]+", "", base.lower().replace("&", "and"))
    key = re.sub(r"segment$", "", key) or key
    return "other" if _RESIDUAL.match(key) else key[:100]


def remove_subtotals(members: dict[str, float], total: float | None) -> dict[str, float]:
    """Drops members that are the sum of other members (e.g. Data Center = Hyperscale + AI Clouds + ...), keeping leaves.

    Parents are removed largest first, and only while the remaining members still reconcile no worse than before.
    """
    remaining = dict(members)
    for member, value in sorted(members.items(), key=lambda item: -abs(item[1])):
        others = {key: other for key, other in remaining.items() if key != member}
        if value <= 0 or len(others) < 2:
            continue
        if not _is_subset_sum([other for other in others.values() if other > 0], value):
            continue
        if total:
            before = abs(sum(remaining.values()) - total)
            after = abs(sum(others.values()) - total)
            if after > before and before <= abs(total) * MAX_RECONCILIATION_ERROR:
                continue
        remaining = others
    return remaining


def _is_subset_sum(values: list[float], target: float, relative_tolerance: float = 0.0005) -> bool:
    """Whether at least two of values sum to target (bitset subset sum on values scaled to ~100k steps)."""
    if len(values) < 2 or target <= 0:
        return False
    unit = 10 ** max(0, math.floor(math.log10(target)) - 5)
    weights = [round(value / unit) for value in values if 0 < value < target * (1 + relative_tolerance)]
    goal = round(target / unit)
    slack = max(len(weights), round(target * relative_tolerance / unit))
    any_sum = 0  # sums of one or more members
    multi_sum = 0  # sums of two or more members
    for weight in weights:
        multi_sum |= any_sum << weight
        any_sum |= (any_sum << weight) | (1 << weight)
    window = ((1 << (2 * slack + 1)) - 1) << max(0, goal - slack)
    return bool(multi_sum & window)


def _member_labels(facts: pd.DataFrame) -> dict[str, str]:
    labels: dict[str, str] = {}
    if "member" in facts.columns and "dimension_member_label" in facts.columns:
        pairs = facts[["member", "dimension_member_label"]].dropna().drop_duplicates("member")
        for member, label in pairs.itertuples(index=False):
            labels[str(member)] = str(label).strip()
    return labels


def _fiscal_year(entity: dict) -> str | None:
    value = entity.get("fiscal_year")
    return str(value) if value else None


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


_SMALL_WORDS = {"and", "of", "or", "the", "by", "for", "in", "to"}


def _title(text: str) -> str:
    """ "markets of customers" → "Markets of Customers"; acronyms and brand casing (HPC, iPhone) stay as written."""
    words = text.split()
    return " ".join(word if not word.islower() else word if index and word in _SMALL_WORDS else word[:1].upper() + word[1:]
                    for index, word in enumerate(words))


def _snake(name: str) -> str:
    base = re.sub(r"Axis$", "", name)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
