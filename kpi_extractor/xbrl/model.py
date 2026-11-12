"""What an XBRL breakdown is: the revenue concepts and axes read, and the groups, candidates and breakdowns found."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date



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
# Intel's 2022-2023 10-Qs tag their segments' revenue on the consolidated-entities axis instead (2026-11-11).
_NEUTRAL_MEMBERS = {("ConsolidationItemsAxis", "OperatingSegmentsMember"), ("ConsolidatedEntitiesAxis", "OperatingSegmentsMember")}


# Segments reported before intersegment sales come out (UnitedHealth's Optum sells to UnitedHealthcare) add up to
# revenue only with the eliminations, tagged on the consolidation axis alone; they are kept as one row.
# Each item on the consolidation axis is its own row under the filing's name (JPMorgan's "Corporate", UnitedHealth's
# "Optum Eliminations"); these names stand in for the taxonomy's own long labels.
_CONSOLIDATION_NAMES = {"IntersegmentEliminationMember": "Eliminations", "CorporateNonSegmentMember": "Corporate",
                        "MaterialReconcilingItemsMember": "Reconciling items",
                        "SegmentReconcilingItemsMember": "Reconciling items"}


# Consolidation items that reconcile segments to revenue (not intersegment eliminations, which sit inside a segment's
# own figure when tagged beside it).
_RECONCILING = {"MaterialReconcilingItemsMember", "SegmentReconcilingItemsMember", "CorporateNonSegmentMember"}


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
