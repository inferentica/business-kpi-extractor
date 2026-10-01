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
                 "SubsegmentsConsolidationItemsAxis", "IncomeStatementLocationAxis", "StatementEquityComponentsAxis"}
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


def extract_breakdowns(facts: pd.DataFrame, entity: dict, form: str,
                       label: Callable[[str], str | None] = lambda _qname: None) -> XbrlBreakdowns | None:
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

    totals: dict[str, float] = {}
    currencies: dict[str, str] = {}
    tagged: list[tuple[str, dict[str, str], float]] = []
    for _, fact in frame.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        dimensions = {axis: member for axis, member in dimensions.items() if (axis, local_name(member)) not in _NEUTRAL_MEMBERS}
        concept = fact["concept"]
        value = float(fact["numeric_value"])
        currency = fact.get("currency") or fact.get("unit_ref")
        if isinstance(currency, str) and currency:
            currencies[concept] = currency.upper().replace("ISO4217:", "")
        if not dimensions:
            totals.setdefault(concept, value)
        else:
            tagged.append((concept, dimensions, value))

    # Candidate breakdowns per axis: facts tagged with that axis alone, and facts tagged with it plus an axis that only
    # ever takes one value alongside it (Netflix tags every region "Streaming"), which then adds nothing.
    candidates: dict[str, list[tuple[str, dict[str, float]]]] = {}
    single: dict[tuple[str, str], dict[str, float]] = {}
    paired: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    for concept, dimensions, value in tagged:
        if any(axis in _IGNORED_AXES for axis in dimensions):
            continue
        if len(dimensions) == 1:
            axis, member = next(iter(dimensions.items()))
            single.setdefault((concept, axis), {}).setdefault(member, value)
        elif len(dimensions) == 2:
            paired.setdefault((concept, frozenset(dimensions)), []).append((dimensions, value))
    for (concept, axis), members in single.items():
        candidates.setdefault(axis, []).append((concept, members))
    for (concept, axes), items in paired.items():
        for fixed in axes:
            if len({dimensions[fixed] for dimensions, _value in items}) != 1:
                continue
            other = next(axis for axis in axes if axis != fixed)
            members: dict[str, float] = {}
            for dimensions, value in items:
                members.setdefault(dimensions[other], value)
            candidates.setdefault(other, []).append((concept, members))

    def total_for(concept: str) -> float | None:
        return totals.get(concept) or next((totals[other] for other in REVENUE_CONCEPTS if other in totals), None)

    def score(candidate: tuple[str, dict[str, float]]):
        concept, members = candidate
        total = total_for(concept)
        leaves = drop_overlaps(remove_subtotals(members, total), total)
        reconciles = bool(total) and abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR
        return (reconciles, len(leaves), concept in totals)

    best_by_axis = {axis: max(options, key=score) for axis, options in candidates.items()}

    groups: list[XbrlGroup] = []
    rejected: list[XbrlCandidate] = []
    for axis, (concept, members) in best_by_axis.items():
        if len(members) < 2:
            continue
        # Every breakdown must add up to reported revenue; one that cannot be checked is not kept.
        total = total_for(concept)
        if not total:
            continue
        standard = _STANDARD_AXES.get(axis)
        axis_label = label(axis_qnames.get(axis, axis)) or humanize(axis)
        key, title, order = standard or (f"x_{_snake(axis)}", f"Revenue by {_title(axis_label)}", 10)

        def named(rows: dict[str, float]) -> list[tuple[str, str, float]]:
            out = []
            for member, value in sorted(rows.items(), key=lambda item: -item[1]):
                name = label(member) or labels.get(member) or humanize(member)
                out.append((member_key(name), name, value))
            return out

        def elements(rows: dict[str, float]) -> dict[str, str]:
            return {member_key(label(member) or labels.get(member) or humanize(member)): member for member in rows}

        leaves = drop_overlaps(remove_subtotals(members, total), total)
        error = (sum(leaves.values()) - total) / total if len(leaves) >= 2 else None
        prior = _by_key(named(_single_axis_members(prior_frame, concept, axis, dimension_columns)))
        ytd = _by_key(named(_single_axis_members(ytd_frame, concept, axis, dimension_columns)))
        ytd_totals = ytd_frame[(ytd_frame["concept"] == concept)
                               & ytd_frame[dimension_columns].isna().all(axis=1)]["numeric_value"] if dimension_columns else []
        ytd_total = float(ytd_totals.iloc[0]) if len(ytd_totals) else None
        prior_ytd = _by_key(named(_single_axis_members(prior_ytd_frame, concept, axis, dimension_columns)))
        prior_ytd_totals = prior_ytd_frame[(prior_ytd_frame["concept"] == concept)
                                           & prior_ytd_frame[dimension_columns].isna().all(axis=1)]["numeric_value"] \
            if dimension_columns else []
        prior_ytd_total = float(prior_ytd_totals.iloc[0]) if len(prior_ytd_totals) else None
        if error is not None and abs(error) <= MAX_RECONCILIATION_ERROR:
            leaf_keys = {k for k, _n, _v in named(leaves)}
            groups.append(XbrlGroup(key, title, order, named(leaves), total, error, currencies.get(concept), concept,
                                    prior, elements(leaves), {k: v for k, v in ytd.items() if k in leaf_keys}, ytd_total,
                                    {k: v for k, v in prior_ytd.items() if k in leaf_keys}, prior_ytd_total))
            continue
        # Worth an AI review only when it could plausibly be a breakdown: a standard axis, or a custom one whose rows
        # are of the right order of magnitude.
        positive = sum(value for value in members.values() if value > 0)
        if 3 <= len(members) <= 40 and (standard or 0.5 * total <= positive <= 3 * total):
            rejected.append(XbrlCandidate(key, title, order, concept, total, currencies.get(concept),
                                          {k: (n, v) for k, n, v in named(members)}, prior, elements(members),
                                          ytd, ytd_total, prior_ytd, prior_ytd_total))
    groups.sort(key=lambda group: (group.order, group.key))
    total_revenue = next((totals[concept] for concept in REVENUE_CONCEPTS if concept in totals), None)
    return XbrlBreakdowns(period_end, annual, _fiscal_year(entity), groups, total_revenue, rejected)


def _by_key(rows: list[tuple[str, str, float]]) -> dict[str, float]:
    """Values by member key, adding rows whose labels share a key ("Other" and "All other") instead of keeping one."""
    out: dict[str, float] = {}
    for key, _label, value in rows:
        out[key] = out.get(key, 0.0) + value
    return out


def _single_axis_members(frame: pd.DataFrame, concept: str, axis: str, dimension_columns: list[str]) -> dict[str, float]:
    """Members of one axis for one concept, from facts tagged with that axis alone."""
    members: dict[str, float] = {}
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        dimensions = {a: m for a, m in dimensions.items() if (a, local_name(m)) not in _NEUTRAL_MEMBERS}
        if len(dimensions) == 1 and axis in dimensions:
            members.setdefault(dimensions[axis], float(fact["numeric_value"]))
    return members


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
